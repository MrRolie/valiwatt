"""The page's data (docs/plan-live.md, build step 3): live/site/data.json, rewritten after every capture. Private:
the page is served on the owner's Tailscale address only, until his ruling on republishing Hydro-Québec's CC BY-NC
data. Nothing here forecasts: it reads the issue log and the captured feed."""
from __future__ import annotations

import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd

from live.daily import captured_readings, drop_fills, hourly, lst_today
from live.issue import LOG, TAG, tagged_record
from valiwatt.forecast.features import day_origin
from valiwatt.paths import REPO_ROOT

SITE = REPO_ROOT / "live" / "site"
OUT = SITE / "data.json"
PEAKS_URL = "https://donnees.hydroquebec.com/api/explore/v2.1/catalog/datasets/evenements-pointe/records"
VERDICT_FROM = "2026-12-01"
MODES = ("official", "experimental")


def read_log() -> dict:
    if not LOG.exists():
        return {}
    return {(x := json.loads(line))["day"]: x for line in LOG.read_text().splitlines() if line}


def scored(issue: dict, actual: pd.Series) -> dict:
    """Per mode, over the hours with a forecast and a measured value: hours, in range, MAPE, mean signed error."""
    out = {}
    hours = pd.DataFrame(issue["hours"])
    hours.index = pd.to_datetime(hours["hour_ending_utc"], utc=True)
    a = actual.reindex(hours.index)
    for m in MODES:
        f = pd.to_numeric(hours[m], errors="coerce")
        lo, hi = pd.to_numeric(hours[f"{m}_lower"], errors="coerce"), pd.to_numeric(hours[f"{m}_upper"], errors="coerce")
        ok = f.notna() & a.notna()
        if not ok.any():
            out[m] = {"n": 0}
            continue
        e = (a[ok] - f[ok]) / a[ok]
        out[m] = {"n": int(ok.sum()), "in_range": int(((a[ok] >= lo[ok]) & (a[ok] <= hi[ok])).sum()),
                  "mape": float(100 * e.abs().mean()), "bias": float(100 * e.mean())}
    return out


def peak_events(now: pd.Timestamp) -> list[dict]:
    from valiwatt.sources.http import get
    try:
        since = (now - pd.Timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = get(PEAKS_URL, params={"where": f"datefin >= '{since}'", "order_by": "datedebut", "limit": 100}, timeout=60)
        return [{k: x[k] for k in ("offre", "datedebut", "datefin", "secteurclient")} for x in r.json()["results"]]
    except Exception as e:  # noqa: BLE001  the page shows the events as unavailable, never stale ones as current
        return [{"error": f"{type(e).__name__}"}]


def payload(now: pd.Timestamp) -> dict:
    day = lst_today(now)
    o = day_origin(day)
    readings = drop_fills(captured_readings())
    seen = readings[readings["mw"].notna()]
    act = hourly(readings).set_index("ts_utc")["mw"]
    log = read_log()
    today = log.get(str(day.date()))
    q = readings[(readings["ts_utc"] > o) & (readings["ts_utc"] <= o + pd.Timedelta(hours=24))]
    days = []
    for k in range(30, -1, -1):
        d = str((day - pd.Timedelta(days=k)).date())
        x = log.get(d)
        days.append({"day": d, "status": "missed" if x is None or x.get("missed") else "issued",
                     **({"score": scored(x, act)} if x and not x.get("missed") else {})})
    rec = tagged_record()
    return {
        "generated_at": now.isoformat(), "day": str(day.date()), "origin": o.isoformat(),
        "latest_reading": seen["ts_utc"].max().isoformat() if len(seen) else None,
        "readings": [[t.isoformat(), None if not np.isfinite(v) else float(v)] for t, v in zip(q["ts_utc"], q["mw"])],
        "hourly": [[t.isoformat(), float(v)] for t, v in act[(act.index > o) & (act.index <= o + pd.Timedelta(hours=24))].items()],
        "issue": today, "days": days, "peaks": peak_events(now),
        "freeze": {"tag": TAG, "official_sha256": rec["official"]["sha256"],
                   "experimental_sha256": rec["experimental"]["sha256"], "q": rec["official"]["q"],
                   "groups": rec["experimental"]["groups"]},
        "verdict_from": VERDICT_FROM,
        **updates_of(str(day.date())),
    }


def updates_of(day: str) -> dict:
    """Line 3 on the page: the day's newest update and the midnight peak paths (live/updates.py). A display: any failure
    here leaves the rest of the page as it was."""
    try:
        from live.updates import LOG as ULOG
        if not ULOG.exists():
            return {}
        ups = [x for x in (json.loads(line) for line in ULOG.read_text().splitlines() if line) if x.get("day") == day]
        if not ups:
            return {}
        newest = max(ups, key=lambda x: x["k"])
        midnight = next((x for x in ups if x["k"] == 0), None)
        return {"update": newest, "midnight_peak": midnight["peak"] if midnight else None}
    except Exception as exc:  # noqa: BLE001 (a display never breaks the page)
        return {"update_error": str(exc)[:200]}


def write(now: pd.Timestamp | None = None) -> None:
    body = json.dumps(payload(pd.Timestamp.now(tz="UTC") if now is None else now), default=str)
    SITE.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=SITE, suffix=".tmp")
    with os.fdopen(fd, "w") as fh:
        fh.write(body)
    os.replace(tmp, OUT)


def demo_days(day: str, raw: pd.Series, today_hours: pd.Series) -> list[dict]:
    """The demo's 30 previous days, each issued as the replay issues it (live.issue.issue on AsOf.issued rows, the
    frozen files of the tag) and scored on its measured hours by `scored`, as the live page's days are; the demo day
    itself on its hours up to the demo's "now". Seen 2025 days: layout only."""
    from live.freeze import LAST, THROUGH
    from live.issue import issue, load_frozen, record
    from valiwatt.forecast.validate import DQ_EXPERIMENT, design_days, forecast_inputs, temperature_sources
    from valiwatt.monitor.run import load_raw
    from valiwatt.paths import INTERIM
    from valiwatt.quality import run as qrun
    from valiwatt.tracking import latest_run

    dq = latest_run(DQ_EXPERIMENT, f"tags.kind = 'dq' and tags.gates = 'passed' and attributes.status = 'FINISHED' "
                    f"and params.through = '{THROUGH}'")
    rawd, _ = load_raw(dq)
    weather = pd.read_parquet(INTERIM / "weather.parquet")
    from valiwatt.forecast.features import AsOf
    asof = AsOf(rawd, last=LAST)
    temp = temperature_sources(weather[weather.index <= LAST])["primary"]
    table = asof.issued(temp, forecast_inputs(qrun.load_runs(dq, years=range(2019, THROUGH + 1)), design_days(LAST), last=LAST))
    official, rule = load_frozen()
    out = []
    for k in range(30, -1, -1):
        d = pd.Timestamp(day) - pd.Timedelta(days=k)
        rows = table[table["day"] == d]
        body = record(d, day_origin(d), official, rule, issue(official, rule, rows))
        actual = today_hours if k == 0 else raw
        out.append({"day": str(d.date()), "status": "issued", "score": scored(body, actual)})
    return out


def demo(day: str = "2025-01-21", now_hour: int = 18) -> None:
    """live/site/demo.json: the rehearsal issue of a seen 2025 day with that day's hourly demand up to `now_hour`
    (no 15-minute readings exist for 2025: the curve is hourly). For the page's layout only, labelled as a demo."""
    from live.daily import history
    lines = [json.loads(x) for x in (LOG.parent / "rehearsal.jsonl").read_text().splitlines() if x]
    issue = next(x for x in lines if x["day"] == day)
    o = day_origin(pd.Timestamp(day))
    raw = history().dropna(subset=["mw"]).drop_duplicates("ts_utc", keep="last").sort_values("ts_utc").set_index("ts_utc")["mw"]
    hrs = raw[(raw.index > o) & (raw.index <= o + pd.Timedelta(hours=now_hour))]
    rec = tagged_record()
    body = {"generated_at": str(o + pd.Timedelta(hours=now_hour)), "day": day, "origin": o.isoformat(),
            "latest_reading": (o + pd.Timedelta(hours=now_hour)).isoformat(),
            "readings": [[t.isoformat(), float(v)] for t, v in hrs.items()],
            "hourly": [[t.isoformat(), float(v)] for t, v in hrs.items()], "issue": issue,
            "days": demo_days(day, raw, hrs),
            "peaks": [], "freeze": {"tag": TAG, "official_sha256": rec["official"]["sha256"],
                                     "experimental_sha256": rec["experimental"]["sha256"], "q": rec["official"]["q"],
                                     "groups": rec["experimental"]["groups"]}, "verdict_from": VERDICT_FROM, "demo": True}
    body.update(demo_updates(issue, raw, now_hour))
    (SITE / "demo.json").write_text(json.dumps(body, default=str))


def demo_updates(issue: dict, raw: pd.Series, now_hour: int) -> dict:
    """Line 3 for the demo: the update at k = now_hour and the midnight peak paths, from the frozen update files, on the
    seen day's measured hours up to now_hour. Layout only."""
    from live.updates import load, make
    params, e_lib, doy = load(tagged=False)
    labels = [pd.Timestamp(h["hour_ending_utc"]) for h in issue["hours"]]
    actual = np.array([raw.get(t, np.nan) for t in labels], dtype=float)
    o = pd.Timestamp(issue["origin"])
    k = make(issue["day"], now_hour, o + pd.Timedelta(hours=now_hour, minutes=25), issue, actual, params, e_lib, doy)
    m = make(issue["day"], 0, o + pd.Timedelta(minutes=25), issue, actual, params, e_lib, doy)
    return {"update": k, "midnight_peak": m["peak"]}


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        demo()
        print("written", SITE / "demo.json")
        sys.exit(0)
    write()
    print("written", OUT)
    sys.exit(0)
