"""Issue a day's live forecasts from the frozen files (docs/plan-live.md): the official forecast with v2's symmetric
range, and the experimental forecast (official × (1 + group median)) with rule (c)'s range, both per hour. Each issue
is one JSON line, hashed, appended to the log before the first hour it forecasts (`append_log` refuses otherwise).

`uv run python -m live.issue --rehearse 2025-01-21` issues a seen day of 2025 from the data-through-2025 tables and
scores it: plumbing only, never a test."""
from __future__ import annotations

import fcntl
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from live.freeze import FROZEN_DIR, RECORD, bin_of
from valiwatt.paths import REPO_ROOT
from valiwatt.forecast.features import day_origin
from valiwatt.forecast.freeze import FrozenModel, FrozenModelError

LOG = FROZEN_DIR.parent / "issues.jsonl"
TAG = "rules-frozen-live"


def tagged_record() -> dict:
    """The freeze record as the tag holds it, never the working tree's copy."""
    out = subprocess.run(["git", "show", f"{TAG}:live/FREEZE.json"], cwd=REPO_ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        raise FrozenModelError(f"no freeze record at tag {TAG}: {out.stderr.strip()}")
    return json.loads(out.stdout)


def load_frozen(tagged: bool = True) -> tuple[FrozenModel, dict]:
    """The frozen files, checked against the freeze record at the tag (tagged=False: the working tree's record, for
    rehearsals before the tag)."""
    official = FrozenModel.from_json((FROZEN_DIR / "official.json").read_text())
    rule = json.loads((FROZEN_DIR / "experimental.json").read_text())
    expected = rule.pop("sha256")
    if hashlib.sha256(json.dumps(rule, sort_keys=True).encode()).hexdigest() != expected:
        raise FrozenModelError("experimental.json does not match its own hash")
    if rule["official_sha256"] != official.sha256():
        raise FrozenModelError("experimental.json was frozen with another official model")
    rule["sha256"] = expected
    rec = tagged_record() if tagged else json.loads(RECORD.read_text())
    if (rec["official"]["sha256"], rec["experimental"]["sha256"]) != (official.sha256(), expected):
        raise FrozenModelError("the frozen files differ from the freeze record live/FREEZE.json")
    return official, rule


def apply_rule(forecast, temp, rule: dict) -> pd.DataFrame:
    """The experimental rule on official forecasts: each hour's group from its forecast temperature by the rule's own
    stored edges, then point = forecast × (1 + median), range = forecast × (1 + lo) to forecast × (1 + hi)."""
    f = np.asarray(forecast, dtype=float)
    group = bin_of(np.asarray(temp, dtype=float), rule["edges_c"], rule["labels"]).map(rule["bin_to_group"])
    g = {k: group.map({lab: v[k] for lab, v in rule["groups"].items()}).astype(float).to_numpy()
         for k in ("lo", "hi", "median")}
    return pd.DataFrame({"group": group.to_numpy(), "experimental": f * (1 + g["median"]),
                         "experimental_lower": f * (1 + g["lo"]), "experimental_upper": f * (1 + g["hi"])})


def issue(official: FrozenModel, rule: dict, rows: pd.DataFrame) -> pd.DataFrame:
    """One row per hour of the day: official forecast and range, experimental forecast and range. rows: the day's
    feature rows as `AsOf.issued` builds them (temp_forecast among them)."""
    p = official.predict(rows)
    exp = apply_rule(p["forecast"].to_numpy(), rows["temp_forecast"].to_numpy(), rule)
    out = pd.DataFrame({"hour_ending_utc": rows.index, "hour": rows["hour"].to_numpy(),
                        "temp_forecast": rows["temp_forecast"].to_numpy(), "official": p["forecast"].to_numpy(),
                        "official_lower": p["lower"].to_numpy(), "official_upper": p["upper"].to_numpy()})
    out = pd.concat([out, exp], axis=1)
    return out


def record(day: pd.Timestamp, issued_at: pd.Timestamp, official: FrozenModel, rule: dict, out: pd.DataFrame) -> dict:
    hours = [{k: (None if isinstance(v, float) and not np.isfinite(v) else
                  (v.isoformat() if isinstance(v, pd.Timestamp) else (round(v, 3) if isinstance(v, float) else v)))
              for k, v in r.items()} for r in out.to_dict("records")]
    body = {"day": str(pd.Timestamp(day).date()), "issued_at": issued_at.isoformat(), "origin": day_origin(day).isoformat(),
            "official_sha256": official.sha256(), "experimental_sha256": rule["sha256"], "hours": hours}
    body["sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    return body


def check_body(body: dict) -> None:
    """A complete issue: 24 consecutive hour-ending labels starting one hour after its origin, its own hash."""
    if pd.Timestamp(body["origin"]) != day_origin(pd.Timestamp(body["day"])):
        raise RuntimeError(f"issue for {body['day']}: its origin is not the day's origin")
    hours = [pd.Timestamp(h["hour_ending_utc"]) for h in body["hours"]]
    expected = [pd.Timestamp(body["origin"]) + pd.Timedelta(hours=k) for k in range(1, 25)]
    if hours != expected:
        raise RuntimeError(f"issue for {body['day']}: its hours are not the 24 hours after its origin")
    rest = {k: v for k, v in body.items() if k != "sha256"}
    if hashlib.sha256(json.dumps(rest, sort_keys=True, default=str).encode()).hexdigest() != body["sha256"]:
        raise RuntimeError(f"issue for {body['day']}: its hash does not match its content")


def append_log(body: dict, now: pd.Timestamp | None = None, log: Path = LOG) -> bool:
    """Append the day's issue unless the deadline has passed or the day already has one. The deadline is the end of
    the day's first hour (docs/plan-live.md, "The daily issue"): an hour's demand exists only at its end. `now` is the
    machine's clock; it is a parameter for tests and rehearsals only. The first issue logged for a day is the one that
    counts: a later one is refused (returns False), never replaces it. The body's issued_at must not be after now."""
    if now is not None and Path(log).resolve() == LOG.resolve():
        raise RuntimeError("the live log takes the machine's clock only")
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    check_body(body)
    first_end = pd.Timestamp(body["hours"][0]["hour_ending_utc"])
    if now >= first_end:
        raise RuntimeError(f"issue for {body['day']} at {now}: its first hour ended at {first_end}")
    if pd.Timestamp(body["issued_at"]) > now:
        raise RuntimeError(f"issue for {body['day']}: issued_at {body['issued_at']} is after now ({now})")
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)  # the duplicate check and the write are one step
        fh.seek(0)
        if any(json.loads(line)["day"] == body["day"] for line in fh.read().splitlines() if line):
            return False
        fh.write(json.dumps(body, sort_keys=True, default=str) + "\n")
        fh.flush()
    return True


def log_missed(day, reason: str, log: Path = LOG) -> bool:
    """Record a day that was not issued, unless it already has a line (an issue or a missed record)."""
    line = {"day": str(pd.Timestamp(day).date()), "missed": True, "reason": reason[:500],
            "at": pd.Timestamp.now(tz="UTC").isoformat()}
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        if any(json.loads(x)["day"] == line["day"] for x in fh.read().splitlines() if x):
            return False
        fh.write(json.dumps(line, sort_keys=True) + "\n")
    return True


def rehearse(day: str) -> int:
    from live.freeze import LAST, THROUGH
    from valiwatt.forecast.features import AsOf
    from valiwatt.forecast.validate import DQ_EXPERIMENT, design_days, forecast_inputs, temperature_sources
    from valiwatt.monitor.run import load_raw
    from valiwatt.paths import INTERIM
    from valiwatt.quality import run as qrun
    from valiwatt.tracking import latest_run

    d = pd.Timestamp(day)
    if d.year != THROUGH:
        raise SystemExit("rehearse on a day of 2025 only")
    dq = latest_run(DQ_EXPERIMENT, f"tags.kind = 'dq' and tags.gates = 'passed' and attributes.status = 'FINISHED' "
                    f"and params.through = '{THROUGH}'")
    raw, _ = load_raw(dq)
    weather = pd.read_parquet(INTERIM / "weather.parquet")
    runs = qrun.load_runs(dq, years=range(2019, THROUGH + 1))
    asof = AsOf(raw, last=LAST)
    temp = temperature_sources(weather[weather.index <= LAST])["primary"]
    forecasts = forecast_inputs(runs, design_days(LAST), last=LAST)
    table = asof.issued(temp, forecasts)  # each day's rows as issued at its own origin
    rows = table[table["day"] == d]
    official, rule = load_frozen(tagged=False)
    out = issue(official, rule, rows)
    body = record(d, day_origin(d), official, rule, out)
    append_log(body, day_origin(d), log=FROZEN_DIR.parent / "rehearsal.jsonl")
    actual = rows["target"].to_numpy()
    for k in ("official", "experimental"):
        a, lo, hi = out[k].to_numpy(), out[f"{k}_lower"].to_numpy(), out[f"{k}_upper"].to_numpy()
        print(f"{k:13s} MAPE {100 * np.nanmean(np.abs(actual - a) / actual):.2f} %  in range "
              f"{int(np.sum((actual >= lo) & (actual <= hi)))}/{len(actual)}  peak forecast {np.nanmax(a):,.0f} MW")
    print(f"actual peak {np.nanmax(actual):,.0f} MW; log line sha256 {body['sha256'][:12]}; groups {sorted(set(out['group']))}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--rehearse":
        sys.exit(rehearse(sys.argv[2]))
    raise SystemExit(__doc__)
