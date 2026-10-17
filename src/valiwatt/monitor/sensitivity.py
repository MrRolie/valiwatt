"""How the rules answer demand the year never held: measured by each replay on its own year (2023, the year the rules
were designed on, and 2024, out of sample), never acted on.

Each measurement runs the same clock-guarded replay and the same decision table as the replay itself; on the held-out
year, each of those replays passes the freeze gate (monitor/replay.py).

- Short episodes. Demand is multiplied by a factor for 1, 3, 5 or 7 days, from every start day from the first judged
  week on, as long as the episode ends at least two weeks before the year's last judged day. An episode is not a model failure, so any *retrain* or
  *recalibrate* it brings is a false alarm. The alarm is *assisted* when one of the weeks it rests on was already
  beyond any of its performance thresholds in the year as it was, and *clean* otherwise. The episodes are
  decided again without the echo exclusion, which counts the alarms the exclusion prevents.
- Level shifts in demand. Demand is multiplied by a factor from the first full week of each season on: weeks until
  *retrain*. The persistence forecast follows demand through its lags, so a shift in demand leaves a smaller gap
  between forecast and demand than its own size; detection power (monitor/run.py) measures the gap itself.
- What the echo exclusion costs. From the first full week of each season, the forecast takes a random error that is
  the same for every hour of a day (a degraded forecast whose weekly bias can change sides), and the rules are run
  with and without the exclusion: runs where the exclusion suppresses or delays *retrain*.

v2 (docs/plan-v2.md, sections 9.2 and 11.3.5): the measures run after each replay has finished, in a diagnostic run
of their own, on the production model only, with the replay's NBM runs and the temperature-drift reference it started
from (passed in, never rebuilt per replay); every replay of 2025 passes the diagnostic run's ID, so each worker process
authorizes itself from the run store and never terminates or changes that run. One more measure, `forecast_run_reuse`:
one feeding run corrupted inside the bounds and made to feed two consecutive days, inside one week and across a week
boundary, in each season, as section 9.2 argues the most a bad forecast run can spoil.
"""

from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from itertools import groupby

import numpy as np
import pandas as pd

from valiwatt.forecast.evaluate import SEASONS
from valiwatt.forecast.features import forecast_temperature
from valiwatt.monitor.replay import RUN_POINTS, ReplayResult, lst_day, origin, replay
from valiwatt.monitor.rules import FAILURES, MIN_DAY_HOURS, RECALIBRATE, RETRAIN, decide, period_stats
from valiwatt.quality.weather import BOUNDS_C
from valiwatt.sources.nbm import AVAILABILITY_LAG, REUSE_LEAD_H

EPISODE_DAYS = (1, 3, 5, 7)
EPISODE_FACTORS = (0.7, 0.85, 1.15, 1.3)
# A replay window spans the episode's weeks, two weeks before (the three-week arm looks back two) and three after
# (the echo, then the weeks a pair or a run of three ending on it needs).
WEEKS_BEFORE, WEEKS_AFTER = 2, 3
# Episodes end at least this many days before the year's last judged day, so their echo is judged too.
ECHO_MARGIN_DAYS = 14
STEP_FACTORS = (0.9, 0.95, 1.05, 1.1)
STEP_HORIZON_WEEKS = 10
NOISE_SDS = (0.06, 0.10, 0.15)
NOISE_SEEDS = 20
NOISE_HORIZON_WEEKS = 8
ALARMS = (RETRAIN, RECALIBRATE)
# Workers start from a fresh process, never a fork: on the held-out year each replay's freeze gate reads the MLflow
# store, and a forked worker would read it through the parent's open SQLite connection.
POOL_CONTEXT = "forkserver"

_INPUTS: dict = {}


def _init(inputs: dict) -> None:
    _INPUTS.clear()
    _INPUTS.update(inputs)


def _map(fn, jobs: list, inputs: dict, workers: int | None) -> list:
    workers = max(1, (os.cpu_count() or 2) - 2) if workers is None else workers
    if workers == 1:
        _init(inputs)
        return [fn(j) for j in jobs]
    with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context(POOL_CONTEXT), initializer=_init,
                             initargs=(inputs,)) as pool:
        return list(pool.map(fn, jobs, chunksize=8))


def baseline(r: ReplayResult) -> dict:
    """Each week of the year as it was: its recommendation, and whether it was out of band on any performance rule."""
    return {w["week_start"]: {"recommendation": d["recommendation"],
                              "flagged": bool(w["evaluated"] and any(w["flags"][k] for k in FAILURES))}
            for w, d in zip(r.weeks, r.decisions, strict=True)}


def alarms(weeks: list[dict], decisions: list[dict], base: dict) -> dict:
    """The weeks where the changed year says *retrain* or *recalibrate* and the year as it was did not, each with the
    weeks it rests on; and the weeks where the rules without the echo exclusion would add *retrain*."""
    out = []
    for d in decisions:
        if d["recommendation"] in ALARMS and base[d["week_start"]]["recommendation"] != d["recommendation"]:
            fired = d["fired"][0]
            rests_on = [e["week_start"] for e in fired["evidence"]]
            out.append({"week_start": d["week_start"], "recommendation": d["recommendation"], "why": fired["why"],
                        "evidence_weeks": rests_on, "assisted": any(base[ws]["flagged"] for ws in rests_on)})
    without = [decide(w, weeks[i - 1] if i else None, weeks[i - 2] if i > 1 else None, echo_exclusion=False)
               for i, w in enumerate(weeks)]
    added = [x["week_start"] for d, x in zip(decisions, without, strict=True)
             if x["recommendation"] == RETRAIN and d["recommendation"] != RETRAIN
             and base[x["week_start"]]["recommendation"] != RETRAIN]
    return {"alarms": out, "retrain_without_echo_exclusion": added}


def _changed(raw: pd.DataFrame, first_day: pd.Timestamp, end_day: pd.Timestamp, factor: float) -> pd.DataFrame:
    """Demand multiplied by `factor` on the local standard days [first_day, end_day)."""
    day = lst_day(pd.DatetimeIndex(raw["ts_utc"]))
    return raw.assign(mw=raw["mw"] * np.where((day >= first_day) & (day < end_day), factor, 1.0))


def _replay_window(raw: pd.DataFrame, first: pd.Timestamp, last: pd.Timestamp, before: int, after: int,
                   runs: pd.DataFrame | None = None):
    """The replay of the weeks around [first, last], with the measure's demand `raw` and, for v2, its NBM runs (`runs`,
    else the replay's), the precomputed temperature-drift reference and the diagnostic run's ID."""
    i = _INPUTS
    lo = max(first - pd.Timedelta(days=first.dayofweek) - pd.Timedelta(weeks=before), i["first_day"])
    hi = min(last + pd.Timedelta(days=6 - last.dayofweek) + pd.Timedelta(weeks=after), i["last_day"])
    if i.get("runs") is None:  # v1's replay exactly
        return replay(raw, i["raw_temperature"], i["model"], i["thresholds"], i["reference"], str(lo.date()),
                      str(hi.date()), i["data_end"], departure=i["departure"])
    return replay(raw, i["raw_temperature"], i["model"], i["thresholds"], i["reference"], str(lo.date()),
                  str(hi.date()), i["data_end"], departure=i["departure"],
                  runs=i["runs"] if runs is None else runs, forecast_reference=i["forecast_reference"],
                  run_id=i["run_id"])


def _episode(job: tuple) -> dict:
    start, days, factor = job
    end = start + pd.Timedelta(days=days)
    r = _replay_window(_changed(_INPUTS["raw_demand"], start, end, factor), start, end - pd.Timedelta(days=1),
                       WEEKS_BEFORE, WEEKS_AFTER)
    return {"start": start, "days": days, "factor": factor, **alarms(r.weeks, r.decisions, _INPUTS["baseline"])}


def _level_shift(job: tuple) -> dict:
    start, factor = job
    end = min(start + pd.Timedelta(weeks=STEP_HORIZON_WEEKS), _INPUTS["last_day"] + pd.Timedelta(days=1))
    r = _replay_window(_changed(_INPUTS["raw_demand"], start, end, factor), start, end - pd.Timedelta(days=1),
                       WEEKS_BEFORE, 0)
    hit = [a["week_start"] for a in alarms(r.weeks, r.decisions, _INPUTS["baseline"])["alarms"]
           if a["recommendation"] == RETRAIN and start <= a["week_start"] < end]
    return {"start": start, "season": SEASONS[(start + pd.Timedelta(days=3)).month], "shift": round(factor - 1, 4),
            "weeks": int((hit[0] - start).days // 7 + 1) if hit else None,
            "weekly_bias": [w["stats"]["rel_bias"] for w in r.weeks if w["evaluated"] and start <= w["week_start"]]}


def _inputs(raw_demand, raw_temperature, model, thresholds, reference, r: ReplayResult, data_end,
            departure: bool = False, runs: pd.DataFrame | None = None, forecast_reference: pd.Series | None = None,
            run_id: str | None = None) -> dict:
    """What each worker receives: the replay's inputs and, for v2, its runs, the temperature-drift reference it
    started from and the diagnostic run's ID (each replay of 2025 passes it, so a worker authorizes itself from the
    store)."""
    out = {"raw_demand": raw_demand, "raw_temperature": raw_temperature, "model": model, "thresholds": thresholds,
           "reference": reference, "data_end": data_end, "baseline": baseline(r),
           "first_day": r.days[0]["day"], "last_day": r.days[-1]["day"], "departure": departure}
    if runs is not None:
        if forecast_reference is None:
            raise ValueError("the v2 measures replay with the temperature-drift reference the replay started from: "
                             "pass it (ReplayResult.temperature_reference), never rebuilt per replay")
        out |= {"runs": runs, "forecast_reference": forecast_reference, "run_id": run_id}
    return out


def _judged_span(r: ReplayResult) -> tuple[pd.Timestamp, pd.Timestamp]:
    judged = [w["week_start"] for w in r.weeks if w["evaluated"]]
    return min(judged), max(judged) + pd.Timedelta(days=6)


def episodes(raw_demand, raw_temperature, model, thresholds, reference, r: ReplayResult, data_end,
             days=EPISODE_DAYS, factors=EPISODE_FACTORS, starts=None, workers: int | None = None,
             departure: bool = False, runs: pd.DataFrame | None = None, forecast_reference: pd.Series | None = None,
             run_id: str | None = None) -> dict:
    """False alarms from short episodes, from every start day whose episode and echo fall in judged weeks."""
    first, last = _judged_span(r)
    jobs = []
    for n in days:
        days_from = starts if starts is not None else pd.date_range(
            first, last - pd.Timedelta(days=n - 1 + ECHO_MARGIN_DAYS), freq="D")
        jobs += [(pd.Timestamp(s), n, f) for s in days_from for f in factors]
    out = _map(_episode, jobs, _inputs(raw_demand, raw_temperature, model, thresholds, reference, r, data_end,
                                       departure, runs, forecast_reference, run_id), workers)
    rows = []
    for (n, f), group in groupby(sorted(out, key=lambda j: (j["days"], j["factor"])),
                                 key=lambda j: (j["days"], j["factor"])):
        group = list(group)

        def count(pred):
            return sum(any(pred(a) for a in j["alarms"]) for j in group)

        rows.append({"days": n, "factor": f, "starts": len(group),
                     "retrain": count(lambda a: a["recommendation"] == RETRAIN),
                     "retrain_clean": count(lambda a: a["recommendation"] == RETRAIN and not a["assisted"]),
                     "recalibrate": count(lambda a: a["recommendation"] == RECALIBRATE),
                     "kept_from_retrain_by_echo_exclusion": sum(
                         bool(j["retrain_without_echo_exclusion"])
                         and not any(a["recommendation"] == RETRAIN for a in j["alarms"]) for j in group)})
    return {"rows": rows, "first_start": first,
            "jobs_with_alarms": [j for j in out if j["alarms"] or j["retrain_without_echo_exclusion"]]}


def _season_starts(r: ReplayResult) -> list[pd.Timestamp]:
    from valiwatt.monitor.run import _season_starts as starts

    return starts(r)


def level_shifts(raw_demand, raw_temperature, model, thresholds, reference, r: ReplayResult, data_end,
                 factors=STEP_FACTORS, starts=None, workers: int | None = None, departure: bool = False,
                 runs: pd.DataFrame | None = None, forecast_reference: pd.Series | None = None,
                 run_id: str | None = None) -> list[dict]:
    """Weeks until *retrain* when demand shifts by a factor from the first full week of each season on."""
    jobs = [(pd.Timestamp(s), f) for s in (starts if starts is not None else _season_starts(r)) for f in factors]
    return _map(_level_shift, jobs, _inputs(raw_demand, raw_temperature, model, thresholds, reference, r, data_end,
                                            departure, runs, forecast_reference, run_id), workers)


def _first_retrain(decisions: list[dict], start: pd.Timestamp, horizon: int) -> int | None:
    hit = [d["week_start"] for d in decisions if d["recommendation"] == RETRAIN
           and start <= d["week_start"] < start + pd.Timedelta(weeks=horizon)]
    return int((hit[0] - start).days // 7 + 1) if hit else None


def echo_exclusion_cost(r: ReplayResult, thresholds: dict, sds=NOISE_SDS, seeds: int = NOISE_SEEDS,
                        horizon: int = NOISE_HORIZON_WEEKS, starts=None) -> dict:
    """Weeks until *retrain*, with and without the echo exclusion, when the forecast takes a random day-level error
    (normal, standard deviation `sd`, one draw per day) from the first full week of each season."""
    from valiwatt.monitor.run import _rescore, weekly_decisions

    runs = []
    for k, start in enumerate(starts if starts is not None else _season_starts(r)):
        start = pd.Timestamp(start)
        late = ((r.hourly["day"] >= start) & (r.hourly["day"] < start + pd.Timedelta(weeks=horizon))).to_numpy()
        day = r.hourly.loc[late, "day"]
        uniq = day.unique()
        for sd in sds:
            for seed in range(seeds):
                err = pd.Series(np.random.default_rng([k, int(round(1000 * sd)), seed]).normal(0, sd, len(uniq)),
                                index=uniq)
                h = r.hourly.copy()
                for col in ("forecast", "lower", "upper"):
                    h.loc[late, col] = h.loc[late, col] * (1 + day.map(err).to_numpy())
                weeks, with_ = weekly_decisions(_rescore(h), r, thresholds)
                without = [decide(w, weeks[i - 1] if i else None, weeks[i - 2] if i > 1 else None,
                                  echo_exclusion=False) for i, w in enumerate(weeks)]
                runs.append({"start": start, "season": SEASONS[(start + pd.Timedelta(days=3)).month], "sd": sd,
                             "seed": seed, "weeks_with": _first_retrain(with_, start, horizon),
                             "weeks_without": _first_retrain(without, start, horizon)})
    rows = []
    for sd, group in groupby(sorted(runs, key=lambda x: x["sd"]), key=lambda x: x["sd"]):
        group = list(group)
        caught = [x for x in group if x["weeks_with"] is not None and x["weeks_without"] is not None]
        rows.append({"sd": sd, "runs": len(group),
                     "retrain_with": sum(x["weeks_with"] is not None for x in group),
                     "retrain_without": sum(x["weeks_without"] is not None for x in group),
                     "suppressed": sum(x["weeks_with"] is None and x["weeks_without"] is not None for x in group),
                     "delayed": sum(x["weeks_with"] > x["weeks_without"] for x in caught),
                     "weeks_delayed": sum(x["weeks_with"] - x["weeks_without"] for x in caught)})
    return {"rows": rows, "horizon_weeks": horizon, "runs": runs}


def measure(raw_demand, raw_temperature, model, thresholds, reference, r: ReplayResult, data_end,
            workers: int | None = None, departure: bool = False, runs: pd.DataFrame | None = None,
            forecast_reference: pd.Series | None = None, run_id: str | None = None) -> dict:
    """The false-alarm and blind-spot measures on the replay's own year. v2: their replays read the replay's NBM runs,
    the temperature-drift reference it started from, and pass the diagnostic run's ID (`run_id`)."""
    args = (raw_demand, raw_temperature, model, thresholds, reference, r, data_end)
    kw = {"workers": workers, "departure": departure}
    if runs is not None:
        kw |= {"runs": runs, "forecast_reference": forecast_reference, "run_id": run_id}
    return {"episodes": episodes(*args, **kw),
            "level_shifts": level_shifts(*args, **kw),
            "echo_exclusion_cost": echo_exclusion_cost(r, thresholds)}


# ---------------------------------------------------------------------- one run fed to two days (plan section 9.2)

REUSE_OFFSETS_C = (5.0, -5.0)
# Where the pair of days (D, D+1) lies: inside one Monday-to-Sunday week (D a Monday to a Saturday), or across a
# week boundary (D a Sunday).
REUSE_PLACEMENTS = ("within_week", "across_weeks")
SEASON_ORDER = ("winter", "spring", "summer", "fall")


def _on_side(day: pd.Timestamp, placement: str) -> bool:
    return day.dayofweek <= 5 if placement == "within_week" else day.dayofweek == 6


def covers_next_day(runs: pd.DataFrame, runtime: pd.Timestamp, next_day: pd.Timestamp) -> bool:
    """Whether the run of `runtime`, on its own points alone, feeds `next_day` by the run rule
    (features.forecast_temperature): its nine bracket points for that day finite and inside the bounds."""
    points = runs.loc[runs["runtime"] == runtime, list(RUN_POINTS)].reset_index(drop=True)
    if points.empty:
        return False
    row = forecast_temperature(points, [next_day]).record.iloc[0]
    return row["reason"] == "" and row["runtime"] == runtime


def reuse_pair(r: ReplayResult, runs: pd.DataFrame, start: pd.Timestamp, placement: str):
    """The placement's pair: the first day D from `start` (the season's first full week) on, with D and D+1 both
    replayed and in the season and D on the placement's side of a week boundary, whose feeding run R (the replay's
    forecast record) is proven to cover D+1 by itself (`covers_next_day`). Returns (D, R), or None when the season
    holds no such pair."""
    season = SEASONS[(pd.Timestamp(start) + pd.Timedelta(days=3)).month]
    record = r.forecast_record
    d = pd.Timestamp(start)
    one = pd.Timedelta(days=1)
    while d in record.index and d + one in record.index and SEASONS[d.month] == season == SEASONS[(d + one).month]:
        runtime = record.loc[d, "runtime"]
        if _on_side(d, placement) and pd.notna(runtime) and covers_next_day(runs, runtime, d + one):
            return d, runtime
        d += one
    return None


def corrupted_runs(runs: pd.DataFrame, runtime: pd.Timestamp, until: pd.Timestamp, offset_c: float) -> pd.DataFrame:
    """The runs with every point of the run of `runtime` moved by `offset_c` °C, clipped so that every point stays
    inside the bounds (so the run still covers what it covered), and every run issued after it and available by
    `until` (the next day's origin) withheld, so that the run feeds both days."""
    available = runs["available_utc"] if "available_utc" in runs else runs["runtime"] + AVAILABILITY_LAG
    lo_f, hi_f = BOUNDS_C[0] * 9 / 5 + 32, BOUNDS_C[1] * 9 / 5 + 32
    out = runs.copy()
    hit = (out["runtime"] == runtime).to_numpy()
    out.loc[hit, "tmp_f"] = (out.loc[hit, "tmp_f"] + offset_c * 9 / 5).clip(lo_f, hi_f)
    withheld = ((runs["runtime"] > runtime) & (available <= until)).to_numpy()
    return out[~withheld].reset_index(drop=True)


def _week_effect(r: ReplayResult, week_start: pd.Timestamp, spoiled: list) -> dict:
    """One affected week: its trimmed statistics beside the mean of its untouched days (the days the corrupted run
    did not feed, with at least MIN_DAY_HOURS scored hours), and the failures whose trimmed statistic lies further out
    than that mean (plan section 9.2 argues none ever does)."""
    w = next(x for x in r.weeks if x["week_start"] == week_start)
    day = r.hourly["day"]
    part = r.hourly[(day >= week_start) & (day < week_start + pd.Timedelta(days=7))].dropna(subset=["ape", "covered"])
    daily = pd.DataFrame([period_stats(g) for d, g in part.groupby("day")
                          if d not in spoiled and len(g) >= MIN_DAY_HOURS])
    untouched = {stat: float(daily[stat].mean()) if len(daily) else float("nan")
                 for stat in ("mape", "rel_bias", "covered")}
    beyond = []
    if w["evaluated"]:
        for k, (stat, _, side) in FAILURES.items():
            t = w["trimmed"][k]
            if np.isfinite(t) and np.isfinite(untouched[stat]) and side * (t - untouched[stat]) > 1e-12:
                beyond.append(k)
    return {"week_start": week_start, "evaluated": w["evaluated"], "trimmed": w["trimmed"],
            "untouched_mean": untouched, "untouched_days": int(len(daily)), "beyond_untouched": beyond}


def _reuse(job: tuple) -> dict:
    """One placement and offset: the replay with the run corrupted and made to feed D and D+1."""
    d, runtime, offset = job
    i = _INPUTS
    nxt = d + pd.Timedelta(days=1)
    runs = corrupted_runs(i["runs"], runtime, origin(nxt), offset)
    r = _replay_window(i["raw_demand"], d, nxt, WEEKS_BEFORE, WEEKS_AFTER, runs=runs)
    fed = [x for x in r.forecast_record.index if r.forecast_record.loc[x, "runtime"] == runtime]
    out = {"offset_c": offset, "days_fed": fed, "shown": fed == [d, nxt]}
    if not out["shown"]:
        return out
    weeks = sorted({x - pd.Timedelta(days=x.dayofweek) for x in (d, nxt)})
    base = i["baseline"]
    out |= {"weeks": [_week_effect(r, ws, [d, nxt]) for ws in weeks],
            "decisions_changed": [{"week_start": x["week_start"], "was": base[x["week_start"]]["recommendation"],
                                   "now": x["recommendation"]} for x in r.decisions
                                  if base[x["week_start"]]["recommendation"] != x["recommendation"]],
            "rule4_fired": any(f["rule"] == 4 for x in r.decisions for f in x["fired"])}
    out["beyond_untouched"] = sum(len(w["beyond_untouched"]) for w in out["weeks"])
    return out


def forecast_run_reuse(raw_demand, raw_temperature, model, thresholds, reference, r: ReplayResult, data_end,
                       runs: pd.DataFrame, forecast_reference: pd.Series, longest_lead_h: float | None = None,
                       workers: int | None = None, departure: bool = False, run_id: str | None = None,
                       offsets=REUSE_OFFSETS_C) -> dict:
    """Plan section 9.2's measure, labelled and never choosing: in each season of the replayed year, two placements
    (`REUSE_PLACEMENTS`), each the first pair of days (D, D+1) from the season's first full week on whose feeding run R
    of D is proven to cover D+1 by itself (`reuse_pair`). R is corrupted by each offset, clipped inside the bounds, and
    the runs issued after it up to D+1's origin are withheld, so that R feeds both days (`corrupted_runs`); the
    replay's forecast record must show R feeding exactly D and D+1, else the placement is not constructible and no
    result of it is reported. A placement with no such pair is recorded not constructible with its reason. Reported:
    the days the run fed, each affected week's trimmed statistics against the mean of its untouched days (any count
    above zero is a finding against the section's argument), the weeks whose decision changes, whether rule 4 fired,
    and the replayed year's longest lead (`longest_lead_h`, from the data-quality run) beside REUSE_LEAD_H. No
    three-day variant: a lead of REUSE_LEAD_H hours or more is a data-quality finding and a section 18 trigger."""
    if r.forecast_record is None:
        raise ValueError("the reuse measure reads a v2 replay's forecast record: replay with the runs")
    starts = {SEASONS[(s + pd.Timedelta(days=3)).month]: s for s in _season_starts(r)}
    placements, jobs = [], []
    for season in SEASON_ORDER:
        for placement in REUSE_PLACEMENTS:
            p = {"season": season, "placement": placement}
            if season not in starts:
                placements.append(p | {"constructible": False, "reason": "no_full_week_of_the_season_replayed"})
                continue
            pair = reuse_pair(r, runs, starts[season], placement)
            if pair is None:
                placements.append(p | {"constructible": False,
                                       "reason": "no_feeding_run_proven_to_cover_the_next_day"})
                continue
            d, runtime = pair
            placements.append(p | {"days": [d, d + pd.Timedelta(days=1)], "runtime": runtime})
            jobs += [(d, runtime, float(o)) for o in offsets]
    inputs = _inputs(raw_demand, raw_temperature, model, thresholds, reference, r, data_end, departure, runs,
                     forecast_reference, run_id)
    results = {(j[0], j[2]): x for j, x in zip(jobs, _map(_reuse, jobs, inputs, workers), strict=True)}
    for p in placements:
        if "days" not in p:
            continue
        mine = [results[(p["days"][0], float(o))] for o in offsets]
        if all(x["shown"] for x in mine):
            p |= {"constructible": True, "offsets": mine}
        else:
            p |= {"constructible": False, "reason": "the_replay_did_not_show_the_run_feeding_both_days",
                  "days_fed": {str(x["offset_c"]): x["days_fed"] for x in mine}}
    made = [p for p in placements if p.get("constructible")]
    return {"label": "sensitivity_never_chooses", "placements": placements, "offsets_c": list(offsets),
            "constructible": len(made),
            "beyond_untouched": sum(x["beyond_untouched"] for p in made for x in p["offsets"]),
            "longest_lead_h": longest_lead_h, "reuse_lead_h": REUSE_LEAD_H}
