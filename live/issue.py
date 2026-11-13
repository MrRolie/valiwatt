"""Issue a day's live forecasts from the frozen files (docs/plan-live.md): the official forecast with v2's symmetric
range, and the experimental forecast (official × (1 + group median)) with rule (c)'s range, both per hour. Each issue
is one JSON line, hashed, appended to the log before the first hour it forecasts (`append_log` refuses otherwise).

`uv run python -m live.issue --rehearse 2025-01-21` issues a seen day of 2025 from the data-through-2025 tables and
scores it: plumbing only, never a test."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from live.freeze import FROZEN_DIR, bin_of
from valiwatt.forecast.features import day_origin
from valiwatt.forecast.freeze import FrozenModel, FrozenModelError

LOG = FROZEN_DIR.parent / "issues.jsonl"


def load_frozen() -> tuple[FrozenModel, dict]:
    official = FrozenModel.from_json((FROZEN_DIR / "official.json").read_text())
    rule = json.loads((FROZEN_DIR / "experimental.json").read_text())
    expected = rule.pop("sha256")
    if hashlib.sha256(json.dumps(rule, sort_keys=True).encode()).hexdigest() != expected:
        raise FrozenModelError("experimental.json does not match its own hash")
    if rule["official_sha256"] != official.sha256():
        raise FrozenModelError("experimental.json was frozen with another official model")
    rule["sha256"] = expected
    return official, rule


def issue(official: FrozenModel, rule: dict, rows: pd.DataFrame) -> pd.DataFrame:
    """One row per hour of the day: official forecast and range, experimental forecast and range. rows: the day's
    feature rows as `AsOf.issued` builds them (temp_forecast among them)."""
    p = official.predict(rows)
    group = bin_of(rows["temp_forecast"].to_numpy()).map(rule["bin_to_group"])
    g = {k: group.map({lab: v[k] for lab, v in rule["groups"].items()}).astype(float).to_numpy()
         for k in ("lo", "hi", "median")}
    f = p["forecast"].to_numpy()
    out = pd.DataFrame({"hour_ending_utc": rows.index, "hour": rows["hour"].to_numpy(),
                        "temp_forecast": rows["temp_forecast"].to_numpy(),
                        "official": f, "official_lower": p["lower"].to_numpy(), "official_upper": p["upper"].to_numpy(),
                        "group": group.to_numpy(), "experimental": f * (1 + g["median"]),
                        "experimental_lower": f * (1 + g["lo"]), "experimental_upper": f * (1 + g["hi"])})
    return out


def record(day: pd.Timestamp, issued_at: pd.Timestamp, official: FrozenModel, rule: dict, out: pd.DataFrame) -> dict:
    hours = [{k: (None if isinstance(v, float) and not np.isfinite(v) else
                  (v.isoformat() if isinstance(v, pd.Timestamp) else (round(v, 3) if isinstance(v, float) else v)))
              for k, v in r.items()} for r in out.to_dict("records")]
    body = {"day": str(pd.Timestamp(day).date()), "issued_at": issued_at.isoformat(), "origin": day_origin(day).isoformat(),
            "official_sha256": official.sha256(), "experimental_sha256": rule["sha256"], "hours": hours}
    body["sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    return body


def append_log(body: dict, now: pd.Timestamp, log: Path = LOG) -> None:
    """Refuses an issue logged at or after the end of its first hour: an hour's demand exists only at its end (v2's
    origin is D 00:00 EST, so a live issue a few minutes after midnight, once D-1's last hour is in, still qualifies)."""
    first_end = min(pd.Timestamp(h["hour_ending_utc"]) for h in body["hours"])
    if now >= first_end:
        raise RuntimeError(f"issue for {body['day']} at {now}: its first hour ended at {first_end}")
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as fh:
        fh.write(json.dumps(body, sort_keys=True, default=str) + "\n")


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
    official, rule = load_frozen()
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
