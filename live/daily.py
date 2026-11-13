"""The daily issue (docs/plan-live.md): day D's feature rows at its origin from the demand history plus the captured
15-minute feed, and the newest NBM runs; then live.issue. `uv run python -m live.daily --check 2025-01-21` builds a
seen 2025 day by this path and compares its forecast with the replay path's (`AsOf.issued`): they must be equal."""
from __future__ import annotations

import gzip
import json
import sys

import numpy as np
import pandas as pd

from live.freeze import THROUGH
from valiwatt.config import data_last
from valiwatt.forecast.features import H, LOOKBACK, AsOf, build, day_origin, forecast_temperature
from valiwatt.paths import REPO_ROOT
from valiwatt.sources import hydroquebec
from valiwatt.sources.nbm import AVAILABILITY_LAG

CAPTURE = REPO_ROOT / "data" / "live" / "capture"
# The issue waits this long after the origin: the feed runs ~22 min behind (measured 2026-11-13).
WAIT = pd.Timedelta(minutes=25)


def captured_readings(origin: pd.Timestamp | None = None) -> pd.DataFrame:
    """Every 15-minute reading captured, the newest capture's value where two disagree; only readings stamped at or
    before `origin` when given."""
    parts = []
    for path in sorted(CAPTURE.glob("*.json.gz")):
        rows = json.loads(gzip.decompress(path.read_bytes()))
        if rows:
            df = pd.DataFrame(rows)
            parts.append(pd.DataFrame({"ts_utc": pd.to_datetime(df["date"], utc=True),
                                       "mw": pd.to_numeric(df["valeurs_demandetotal"], errors="coerce"),
                                       "capture": path.name}))
    if not parts:
        return pd.DataFrame(columns=["ts_utc", "mw"])
    r = pd.concat(parts).sort_values(["ts_utc", "capture"]).drop_duplicates("ts_utc", keep="last")
    if origin is not None:
        r = r[r["ts_utc"] <= pd.Timestamp(origin)]
    return r[["ts_utc", "mw"]].reset_index(drop=True)


FLAT_RUN = 4  # identical consecutive readings at or above this count are a fill, not measurements (2026-11-13)


def drop_fills(readings: pd.DataFrame) -> pd.DataFrame:
    """Blank every reading in a run of FLAT_RUN or more identical consecutive readings: the feed backfills an outage
    with one repeated value (seen 2026-11-13, 15:30-17:00 UTC, seven readings of 24,034 MW)."""
    r = readings.sort_values("ts_utc").reset_index(drop=True).copy()
    step = r["ts_utc"].diff() != pd.Timedelta(minutes=15)
    run_id = (step | (r["mw"] != r["mw"].shift())).cumsum()
    size = r.groupby(run_id)["mw"].transform("size")
    r.loc[(size >= FLAT_RUN) & r["mw"].notna(), "mw"] = np.nan
    return r


def hourly(readings: pd.DataFrame) -> pd.DataFrame:
    """Hour H (hour-ending, UTC) = the mean of the four readings stamped in (H - 1 h, H]; an hour short of four
    readings is left out (docs/plan-live.md, "The live inputs")."""
    if readings.empty:
        return pd.DataFrame(columns=["ts_utc", "mw", "source_line"])
    r = drop_fills(readings).dropna(subset=["mw"])
    hour = r["ts_utc"].dt.ceil("h")
    g = r.groupby(hour)["mw"].agg(["mean", "size"])
    g = g[g["size"] == 4]
    return pd.DataFrame({"ts_utc": g.index, "mw": g["mean"].to_numpy(), "source_line": -1}).reset_index(drop=True)


def history() -> pd.DataFrame:
    raw = hydroquebec.load_raw(hydroquebec.csv_path(THROUGH))
    return raw[raw["ts_utc"] <= data_last(THROUGH)]


def rows_for(day, raw: pd.DataFrame, runs: pd.DataFrame) -> pd.DataFrame:
    """Day D's 24 feature rows as the replay issues them at D's origin: demand as of the origin, D's hours not yet
    observed, the forecast temperature by v2's run rule."""
    day = pd.Timestamp(day)
    o = day_origin(day)
    hours = pd.date_range(o + H, o + 24 * H, freq="h")
    window = pd.date_range(o - LOOKBACK, o + 24 * H, freq="h")
    dem = AsOf(raw, last=o).frame(o).reindex(window)
    dem.loc[hours, ["mw", "status"]] = [np.nan, None]
    forecasts = {"forecast": forecast_temperature(runs, [day], AVAILABILITY_LAG)}
    return build(dem, pd.Series(np.nan, index=window), forecasts).loc[hours]


def check(day: str) -> int:
    from live.issue import issue, load_frozen
    from valiwatt.forecast.validate import DQ_EXPERIMENT, design_days, forecast_inputs, temperature_sources
    from valiwatt.paths import INTERIM
    from valiwatt.quality import run as qrun
    from valiwatt.tracking import latest_run

    d = pd.Timestamp(day)
    dq = latest_run(DQ_EXPERIMENT, f"tags.kind = 'dq' and tags.gates = 'passed' and attributes.status = 'FINISHED' "
                    f"and params.through = '{THROUGH}'")
    runs = qrun.load_runs(dq, years=range(2019, THROUGH + 1))
    raw = history()
    official, rule = load_frozen()
    live_rows = rows_for(d, raw, runs)
    last = data_last(THROUGH)
    asof = AsOf(raw, last=last)
    weather = pd.read_parquet(INTERIM / "weather.parquet")
    temp = temperature_sources(weather[weather.index <= last])["primary"]
    table = asof.issued(temp, forecast_inputs(runs, design_days(last), last=last))
    ref_rows = table[table["day"] == d]
    a, b = issue(official, rule, live_rows), issue(official, rule, ref_rows)
    diff = {c: float(np.nanmax(np.abs(a[c].to_numpy() - b[c].to_numpy()))) for c in ("official", "experimental")}
    same_mask = all((np.isfinite(a[c]) == np.isfinite(b[c])).all() for c in ("official", "experimental"))
    print(f"{day}: live path vs replay path, max abs MW {diff}, same missing hours {same_mask}, "
          f"lags equal {np.allclose(live_rows[['lag24', 'lag168']], ref_rows[['lag24', 'lag168']], equal_nan=True)}")
    return 0 if same_mask and max(diff.values()) < 1e-6 else 1




def live_runs(origin: pd.Timestamp) -> pd.DataFrame:
    """The NBM runs of the 3 days before the origin, as the archive serves them now (same columns as
    sources.nbm.load_runs). The run rule keeps only runs available by the origin."""
    import csv
    import io

    from valiwatt.sources.http import get
    from valiwatt.sources.nbm import MODEL, STATION, URL

    params = {"station": STATION, "model": MODEL, "sts": (origin - pd.Timedelta(days=3)).strftime("%Y-%m-%dT%H:%MZ"),
              "ets": origin.strftime("%Y-%m-%dT%H:%MZ"), "format": "csv"}
    text = get(URL, params=params, timeout=120).content.decode("utf-8-sig")
    rows = [r for r in csv.DictReader(io.StringIO(text)) if r.get("model") == MODEL and r.get("station") == STATION]
    out = pd.DataFrame({"runtime": pd.to_datetime([r["runtime"] for r in rows]).tz_localize("UTC"),
                        "ftime": pd.to_datetime([r["ftime"] for r in rows]).tz_localize("UTC"),
                        "tmp_f": pd.to_numeric(pd.Series([r["tmp"] for r in rows]), errors="coerce").to_numpy()})
    return out.drop_duplicates().sort_values(["runtime", "ftime"]).reset_index(drop=True)


def lst_today(now: pd.Timestamp) -> pd.Timestamp:
    from valiwatt.config import LST_OFFSET
    return (pd.Timestamp(now) + LST_OFFSET).tz_convert(None).normalize()


def run_issue() -> int:
    """Issue today's forecasts (cron, a few minutes after 00:00 EST): log them, commit their hash. Any failure before
    the deadline is logged as a missed day with its reason."""
    from live.issue import issue, load_frozen, record, append_log, log_missed
    from live.commit import commit_hash

    from live.issue import LOG

    now = pd.Timestamp.now(tz="UTC")
    day = lst_today(now)
    o = day_origin(day)
    if now < o + WAIT:
        return 0  # D-1's last reading is not in the feed yet
    if now >= o + pd.Timedelta(hours=1):  # past the deadline: record the day as missed if nothing is logged
        log_missed(day, "not issued by the deadline (machine off or every attempt failed)")
        return 0
    if LOG.exists() and any(json.loads(x)["day"] == str(day.date()) for x in LOG.read_text().splitlines() if x):
        return 0
    try:
        official, rule = load_frozen(tagged=True)
        raw = pd.concat([history(), hourly(captured_readings(o))], ignore_index=True)
        rows = rows_for(day, raw, live_runs(o))
        out = issue(official, rule, rows)
        if not np.isfinite(out["official"]).any():
            raise RuntimeError("no hour has its inputs (missing lags or forecast temperature)")
        body = record(day, pd.Timestamp.now(tz="UTC"), official, rule, out)
        if not append_log(body):
            print(f"{day.date()}: already issued")
            return 0
    except Exception as e:  # noqa: BLE001  every failure is recorded as a missed day
        log_missed(day, f"{type(e).__name__}: {e}")
        print(f"{day.date()}: missed: {e}", file=sys.stderr)
        return 1
    commit_hash(body)
    print(f"{day.date()}: issued {body['sha256'][:12]}, peak official {out['official'].max():,.0f} MW")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        sys.exit(check(sys.argv[2]))
    if sys.argv[1:] == ["--issue"]:
        sys.exit(run_issue())
    raise SystemExit(__doc__)
