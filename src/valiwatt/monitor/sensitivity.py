"""How the rules answer demand the year never held: measured by the pseudo-replay on its own year, never acted on.

Each measurement runs the same clock-guarded replay and the same decision table as the replay itself.

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
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from itertools import groupby

import numpy as np
import pandas as pd

from valiwatt.forecast.evaluate import SEASONS
from valiwatt.monitor.replay import ReplayResult, lst_day, replay
from valiwatt.monitor.rules import FAILURES, RECALIBRATE, RETRAIN, decide

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

_INPUTS: dict = {}


def _init(inputs: dict) -> None:
    _INPUTS.clear()
    _INPUTS.update(inputs)


def _map(fn, jobs: list, inputs: dict, workers: int | None) -> list:
    workers = max(1, (os.cpu_count() or 2) - 2) if workers is None else workers
    if workers == 1:
        _init(inputs)
        return [fn(j) for j in jobs]
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(inputs,)) as pool:
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


def _replay_window(raw: pd.DataFrame, first: pd.Timestamp, last: pd.Timestamp, before: int, after: int):
    i = _INPUTS
    lo = max(first - pd.Timedelta(days=first.dayofweek) - pd.Timedelta(weeks=before), i["first_day"])
    hi = min(last + pd.Timedelta(days=6 - last.dayofweek) + pd.Timedelta(weeks=after), i["last_day"])
    return replay(raw, i["raw_temperature"], i["model"], i["thresholds"], i["reference"], str(lo.date()),
                  str(hi.date()), i["data_end"])


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


def _inputs(raw_demand, raw_temperature, model, thresholds, reference, r: ReplayResult, data_end) -> dict:
    return {"raw_demand": raw_demand, "raw_temperature": raw_temperature, "model": model, "thresholds": thresholds,
            "reference": reference, "data_end": data_end, "baseline": baseline(r),
            "first_day": r.days[0]["day"], "last_day": r.days[-1]["day"]}


def _judged_span(r: ReplayResult) -> tuple[pd.Timestamp, pd.Timestamp]:
    judged = [w["week_start"] for w in r.weeks if w["evaluated"]]
    return min(judged), max(judged) + pd.Timedelta(days=6)


def episodes(raw_demand, raw_temperature, model, thresholds, reference, r: ReplayResult, data_end,
             days=EPISODE_DAYS, factors=EPISODE_FACTORS, starts=None, workers: int | None = None) -> dict:
    """False alarms from short episodes, from every start day whose episode and echo fall in judged weeks."""
    first, last = _judged_span(r)
    jobs = []
    for n in days:
        days_from = starts if starts is not None else pd.date_range(
            first, last - pd.Timedelta(days=n - 1 + ECHO_MARGIN_DAYS), freq="D")
        jobs += [(pd.Timestamp(s), n, f) for s in days_from for f in factors]
    out = _map(_episode, jobs, _inputs(raw_demand, raw_temperature, model, thresholds, reference, r, data_end),
               workers)
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
                 factors=STEP_FACTORS, starts=None, workers: int | None = None) -> list[dict]:
    """Weeks until *retrain* when demand shifts by a factor from the first full week of each season on."""
    jobs = [(pd.Timestamp(s), f) for s in (starts if starts is not None else _season_starts(r)) for f in factors]
    return _map(_level_shift, jobs, _inputs(raw_demand, raw_temperature, model, thresholds, reference, r, data_end),
                workers)


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
            workers: int | None = None) -> dict:
    args = (raw_demand, raw_temperature, model, thresholds, reference, r, data_end)
    return {"episodes": episodes(*args, workers=workers), "level_shifts": level_shifts(*args, workers=workers),
            "echo_exclusion_cost": echo_exclusion_cost(r, thresholds)}
