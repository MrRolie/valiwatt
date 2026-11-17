"""Line 3, the updates (docs/plan-live.md, "Line 3, the updates", declared at a4a9ef5).

At each complete hour k of day D, re-issue D's later hours from the official midnight forecast F:
U(k, h) = F(h) × (1 + ē(k) × ρ^(h − k)), with a range by lead and 1,000 sampled whole-day error paths for the peak.
Its own frozen files, record, tag, log and cron step: nothing here can block or change the midnight issue.

  uv run python -m live.updates --freeze              design on 2020-2024, check on 2025, write the frozen files
  uv run python -m live.updates --run                 the cron step: issue the update that is due, if any
  uv run python -m live.updates --rehearse 2025-01-21 a seen day of 2025, every k, plumbing only"""
from __future__ import annotations

import fcntl
import hashlib
import itertools
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from live.freeze import FROZEN_DIR
from valiwatt.forecast.features import day_origin
from valiwatt.forecast.freeze import FrozenModelError
from valiwatt.paths import REPO_ROOT

TAG = "live-updates-frozen"
PARAMS = FROZEN_DIR / "updates.json"
LIB = FROZEN_DIR / "updates_lib.npz"
RECORD = REPO_ROOT / "live" / "UPDATES-FREEZE.json"
LOG = FROZEN_DIR.parent / "updates.jsonl"
OOS = REPO_ROOT / "data" / "analysis" / "live-updates" / "out" / "oos.parquet"
H = pd.Timedelta(hours=1)
WAIT = pd.Timedelta(minutes=25)

# the declaration (docs/plan-live.md)
RHOS = (0.80, 0.85, 0.90, 0.93, 0.95, 0.97, 0.99, 1.00)
MS = (1, 2, 3)
ALPHA, NMIN = 0.10, 2000
N_PATHS, WINDOW_DAYS = 1000, 30
DESIGN_YEARS, CHECK_YEAR = range(2020, 2025), 2025


# ---- the rule ------------------------------------------------------------------------------------------------------
def ebar(e: np.ndarray, k: int, m: int) -> np.ndarray:
    """Mean relative error of the last m complete hours up to k. e: (days, 24), NaN where an hour is not complete or has
    no forecast; positions 0..k-1 are D's hours 1..k. The hours used are the last m finite ones at or before k, never
    before D's origin; none: 0 (U = F)."""
    e = np.atleast_2d(e)
    out = np.zeros(e.shape[0])
    for i, row in enumerate(e):
        known = row[:k][np.isfinite(row[:k])]
        out[i] = known[-m:].mean() if known.size else 0.0
    return out


def updated(F: np.ndarray, eb: np.ndarray, k: int, rho: float) -> tuple[np.ndarray, np.ndarray]:
    """U(k, h) for h = k+1..24 (positions k..23) and their leads h - k."""
    lead = np.arange(1, 25 - k)
    return np.atleast_2d(F)[:, k:] * (1 + np.asarray(eb)[:, None] * rho ** lead[None, :]), lead


# ---- the design (run once, as declared) -----------------------------------------------------------------------------
def day_matrix(oos: pd.DataFrame):
    o = oos.sort_index()
    F, A, days = [], [], []
    for d, g in o.groupby("day"):
        if len(g) != 24 or g["pred_gam_forecast"].isna().any() or g["target"].isna().any():
            continue
        F.append(g["pred_gam_forecast"].to_numpy()); A.append(g["target"].to_numpy()); days.append(pd.Timestamp(d))
    return np.array(F), np.array(A), pd.DatetimeIndex(days)


def mape_pairs(F, A, m, rho) -> float:
    e = (A - F) / F
    num, n = 0.0, 0
    for k in range(1, 24):
        U, _ = updated(F, ebar(e, k, m), k, rho)
        ape = np.abs(A[:, k:] - U) / A[:, k:]
        num += ape.sum(); n += ape.size
    return 100 * num / n


def choose(F, A):
    grid = {(m, rho): mape_pairs(F, A, m, rho) for m, rho in itertools.product(MS, RHOS)}
    return min(grid, key=lambda t: (round(grid[t], 10), t[0], t[1])), grid  # ties: smaller m, then smaller rho


def lead_range(F, A, m, rho) -> dict:
    e = (A - F) / F
    pooled = {l: [] for l in range(1, 24)}
    for k in range(1, 24):
        U, lead = updated(F, ebar(e, k, m), k, rho)
        r = (A[:, k:] - U) / U
        for j, l in enumerate(lead):
            pooled[int(l)].append(r[:, j])
    pooled = {l: np.concatenate(v) for l, v in pooled.items()}
    groups, cur = {}, []
    for l in range(23, 0, -1):  # a thin lead merges into the next shorter one
        cur.append(l)
        if sum(len(pooled[x]) for x in cur) >= NMIN:
            for x in cur:
                groups[x] = tuple(sorted(cur))
            cur = []
    if cur:
        g = tuple(sorted(set(groups.get(1, ())) | set(cur)))
        for x in g:
            groups[x] = g
    out = {}
    for l in range(1, 24):
        v = np.concatenate([pooled[x] for x in groups[l]])
        out[str(l)] = {"lo": float(np.quantile(v, ALPHA / 2)), "hi": float(np.quantile(v, 1 - ALPHA / 2)),
                       "n": int(len(v)), "group": list(groups[l])}
    return out


def coverage(F, A, m, rho, rng: dict) -> float:
    e = (A - F) / F
    inside = n = 0
    for k in range(1, 24):
        U, lead = updated(F, ebar(e, k, m), k, rho)
        lo = U * (1 + np.array([rng[str(l)]["lo"] for l in lead]))
        hi = U * (1 + np.array([rng[str(l)]["hi"] for l in lead]))
        c = (A[:, k:] >= lo) & (A[:, k:] <= hi)
        inside += int(c.sum()); n += c.size
    return inside / n


def segment_mape(F, A, m, rho, k) -> tuple[float, float]:
    e = (A - F) / F
    U, _ = updated(F, ebar(e, k, m), k, rho)
    return (100 * float((np.abs(A[:, k:] - F[:, k:]) / A[:, k:]).mean()),
            100 * float((np.abs(A[:, k:] - U) / A[:, k:]).mean()))


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def freeze() -> dict:
    oos = pd.read_parquet(OOS)
    if pd.to_datetime(oos["day"]).max() >= pd.Timestamp("2026-01-01"):
        raise RuntimeError("the folds hold 2026 data")
    F, A, days = day_matrix(oos)
    dmask, tmask = days.year.isin(list(DESIGN_YEARS)), days.year == CHECK_YEAR
    (m, rho), grid = choose(F[dmask], A[dmask])
    rng = lead_range(F[dmask], A[dmask], m, rho)
    noon = segment_mape(F[tmask], A[tmask], m, rho, 12)
    six = segment_mape(F[tmask], A[tmask], m, rho, 6)
    if noon[1] >= noon[0]:
        raise RuntimeError(f"stop (declared check on 2025): the noon update does not lower MAPE: {noon}")
    e_lib = (A[dmask] - F[dmask]) / F[dmask]
    doy = days[dmask].dayofyear.to_numpy()
    FROZEN_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(LIB, e=e_lib, doy=doy)
    lib_sha = hashlib.sha256(np.ascontiguousarray(e_lib).tobytes() + np.ascontiguousarray(doy).tobytes()).hexdigest()
    params = {"m": int(m), "rho": float(rho), "alpha": ALPHA, "range_by_lead": rng, "n_paths": N_PATHS,
              "window_days": WINDOW_DAYS, "lib_sha256": lib_sha, "base": "live-frozen official"}
    params["sha256"] = _sha(params)
    PARAMS.write_text(json.dumps(params, indent=1, sort_keys=True))
    rec = {
        "declared_at": "a4a9ef5", "oos_sha256": hashlib.sha256(OOS.read_bytes()).hexdigest(),
        "design_years": [min(DESIGN_YEARS), max(DESIGN_YEARS)], "design_days": int(dmask.sum()),
        "choice": {"m": int(m), "rho": float(rho), "design_mape_pairs": round(grid[(m, rho)], 4)},
        "grid": {f"m={a} rho={b}": round(v, 4) for (a, b), v in sorted(grid.items())},
        "per_year_choice_deciding_nothing": {str(y): list(choose(F[days.year == y], A[days.year == y])[0])
                                             for y in DESIGN_YEARS},
        "coverage_design": round(coverage(F[dmask], A[dmask], m, rho, rng), 4),
        "check_2025_seen_deciding_only_a_stop": {
            "days": int(tmask.sum()),
            "noon_hours_13_24_mape": {"midnight": round(noon[0], 4), "update": round(noon[1], 4)},
            "six_hours_7_24_mape": {"midnight": round(six[0], 4), "update": round(six[1], 4)},
            "coverage": round(coverage(F[tmask], A[tmask], m, rho, rng), 4)},
        "params_sha256": params["sha256"], "lib_sha256": lib_sha,
    }
    RECORD.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    return rec


# ---- the frozen files at run time -----------------------------------------------------------------------------------
def tagged_record() -> dict:
    out = subprocess.run(["git", "show", f"{TAG}:live/UPDATES-FREEZE.json"], cwd=REPO_ROOT, capture_output=True,
                         text=True)
    if out.returncode != 0:
        raise FrozenModelError(f"no updates record at tag {TAG}: {out.stderr.strip()}")
    return json.loads(out.stdout)


def load(tagged: bool = True) -> tuple[dict, np.ndarray, np.ndarray]:
    params = json.loads(PARAMS.read_text())
    sha = params.pop("sha256")
    if _sha(params) != sha:
        raise FrozenModelError("updates.json does not match its own hash")
    params["sha256"] = sha
    lib = np.load(LIB)
    e, doy = lib["e"], lib["doy"]
    if hashlib.sha256(np.ascontiguousarray(e).tobytes() + np.ascontiguousarray(doy).tobytes()).hexdigest() \
            != params["lib_sha256"]:
        raise FrozenModelError("updates_lib.npz differs from updates.json")
    rec = tagged_record() if tagged else json.loads(RECORD.read_text())
    if (rec["params_sha256"], rec["lib_sha256"]) != (sha, params["lib_sha256"]):
        raise FrozenModelError("the frozen update files differ from live/UPDATES-FREEZE.json")
    return params, e, doy


# ---- one update -----------------------------------------------------------------------------------------------------
def peak_paths(F: np.ndarray, A: np.ndarray, k: int, params: dict, e_lib, doy_lib, day, eb: float) -> dict:
    """1,000 paths of D's remaining hours from whole design days within ±window of D's calendar date: the updated
    relative error r = (e − ē_d ρ^ℓ)/(1 + ē_d ρ^ℓ) of one design day at the same k. Peak = max(measured hours ≤ k, path)."""
    m, rho = params["m"], params["rho"]
    d = pd.Timestamp(day)
    dist = np.abs(doy_lib - d.dayofyear)
    pool = np.flatnonzero(np.minimum(dist, 366 - dist) <= params["window_days"])
    seed = int(hashlib.sha256(f"{d.date()}|{k}".encode()).hexdigest()[:8], 16)
    pick = np.random.default_rng(seed).choice(pool, size=params["n_paths"], replace=True)
    lead = np.arange(1, 25 - k)
    ed = e_lib[pick]
    ebd = ebar(ed, k, m) if k else np.zeros(len(pick))
    damp = ebd[:, None] * rho ** lead[None, :]
    r = (ed[:, k:] - damp) / (1 + damp)
    U, _ = updated(F, np.array([eb]), k, rho)
    paths = U * (1 + r)  # (n, 24-k)
    meas = A[:k] if k else np.array([])
    meas_max = np.nanmax(meas) if np.isfinite(meas).any() else -np.inf
    meas_hour = int(np.nanargmax(meas)) + 1 if np.isfinite(meas).any() else None
    pmax = np.nanmax(np.where(np.isfinite(paths), paths, -np.inf), axis=1)
    phour = np.nanargmax(np.where(np.isfinite(paths), paths, -np.inf), axis=1) + k + 1
    peak = np.maximum(pmax, meas_max)
    hour = np.where(pmax >= meas_max, phour, meas_hour if meas_hour else phour)
    vals, counts = np.unique(hour, return_counts=True)
    q = np.quantile(peak, [0.1, 0.5, 0.9])
    return {"p10": round(float(q[0]), 1), "p50": round(float(q[1]), 1), "p90": round(float(q[2]), 1),
            "hour_ending": int(vals[np.argmax(counts)])}


def make(day, k: int, issued_at: pd.Timestamp, issue_body: dict, actual: np.ndarray, params: dict, e_lib,
         doy_lib) -> dict:
    """The update at complete hour k (0: the midnight paths only, U = F). issue_body: the day's midnight issue;
    actual: D's 24 hourly values, NaN where not complete."""
    F = np.array([np.nan if h["official"] is None else h["official"] for h in issue_body["hours"]], dtype=float)
    A = np.asarray(actual, dtype=float).copy()
    A[k:] = np.nan  # never a value after k
    e = (A - F) / F
    eb = float(ebar(e[None, :], k, params["m"])[0]) if k else 0.0
    U, lead = updated(F, np.array([eb]), k, params["rho"])
    U = U[0]
    hours = []
    if k:  # k = 0 carries the peak paths only: the midnight issue already holds its hours
        lo = U * (1 + np.array([params["range_by_lead"][str(l)]["lo"] for l in lead]))
        hi = U * (1 + np.array([params["range_by_lead"][str(l)]["hi"] for l in lead]))
        hours = [{"hour_ending_utc": issue_body["hours"][k + j]["hour_ending_utc"],
                  "update": None if not np.isfinite(U[j]) else round(float(U[j]), 3),
                  "lower": None if not np.isfinite(lo[j]) else round(float(lo[j]), 3),
                  "upper": None if not np.isfinite(hi[j]) else round(float(hi[j]), 3)} for j in range(len(U))]
    body = {"day": issue_body["day"], "k": int(k), "issued_at": pd.Timestamp(issued_at).isoformat(),
            "origin": issue_body["origin"], "issue_sha256": issue_body["sha256"], "updates_sha256": params["sha256"],
            "ebar": round(eb, 6), "hours": hours,
            "peak": peak_paths(F, A, k, params, e_lib, doy_lib, issue_body["day"], eb)}
    body["sha256"] = _sha(body)
    return body


def deadline(body: dict) -> pd.Timestamp:
    """Update k counts only if issued before hour k + 1 ends (k = 0: before hour 1 ends, as the midnight issue)."""
    return pd.Timestamp(body["origin"]) + (body["k"] + 1) * H


def append(body: dict, now: pd.Timestamp | None = None, log: Path = LOG) -> bool:
    if now is not None and Path(log).resolve() == LOG.resolve():
        raise RuntimeError("the live updates log takes the machine's clock only")
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    rest = {k: v for k, v in body.items() if k != "sha256"}
    if _sha(rest) != body["sha256"]:
        raise RuntimeError("update: its hash does not match its content")
    if pd.Timestamp(body["origin"]) != day_origin(pd.Timestamp(body["day"])):
        raise RuntimeError("update: its origin is not the day's origin")
    if now >= deadline(body):
        raise RuntimeError(f"update {body['day']} k={body['k']} at {now}: deadline {deadline(body)} passed")
    if pd.Timestamp(body["issued_at"]) > now:
        raise RuntimeError("update: issued_at is after now")
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        if any((x := json.loads(line)).get("day") == body["day"] and x.get("k") == body["k"]
               for line in fh.read().splitlines() if line):
            return False
        fh.write(json.dumps(body, sort_keys=True) + "\n")
        fh.flush()
    return True


# ---- the cron step --------------------------------------------------------------------------------------------------
def due(origin: pd.Timestamp, complete: np.ndarray, done: set, now: pd.Timestamp) -> int | None:
    """The update due now: the newest k (0..23) whose hour k is complete (k = 0 always), not yet logged, issued at or
    after hour k's end + 25 min and before its deadline."""
    for k in range(23, -1, -1):
        if k and not complete[k - 1]:
            continue
        if k in done:
            return None  # the newest one is out; older ones are past their deadline or superseded
        if origin + k * H + WAIT <= now < origin + (k + 1) * H:
            return k
    return None


def run() -> int:
    from live.commit import commit_hash
    from live.daily import captured_readings, drop_fills, hourly, lst_today
    from live.issue import LOG as ISSUES

    now = pd.Timestamp.now(tz="UTC")
    day = lst_today(now)
    origin = day_origin(day)
    issues = [json.loads(x) for x in ISSUES.read_text().splitlines() if x] if ISSUES.exists() else []
    body = next((b for b in issues if b.get("day") == str(day.date()) and not b.get("missed")), None)
    if body is None:
        return 0  # no midnight issue, no update
    hv = hourly(drop_fills(captured_readings()))
    s = hv.set_index("ts_utc")["mw"]
    labels = [pd.Timestamp(h["hour_ending_utc"]) for h in body["hours"]]
    actual = np.array([s.get(t, np.nan) for t in labels], dtype=float)
    done = {json.loads(x)["k"] for x in LOG.read_text().splitlines()
            if x and json.loads(x).get("day") == body["day"]} if LOG.exists() else set()
    k = due(origin, np.isfinite(actual), done, now)
    if k is None:
        return 0
    params, e_lib, doy = load()
    up = make(day, k, now, body, actual, params, e_lib, doy)
    if append(up):
        commit_hash({"day": f"{up['day']} update k={k}", "sha256": up["sha256"], "issued_at": up["issued_at"]})
        print(f"{up['day']} update k={k}: ebar {up['ebar']:+.4f}, peak P50 {up['peak']['p50']}")
    return 0


def rehearse(day: str) -> int:
    """A seen day of 2025 from the walk-forward folds: every k from 0 to 23, appended to a scratch log with the
    hour's own clock. Plumbing only, never a test."""
    import tempfile
    params, e_lib, doy = load(tagged=False)
    oos = pd.read_parquet(OOS)
    g = oos[oos["day"] == pd.Timestamp(day)].sort_index()
    origin = day_origin(pd.Timestamp(day))
    issue_body = {"day": day, "origin": origin.isoformat(), "sha256": "rehearsal",
                  "hours": [{"hour_ending_utc": t.isoformat(), "official": float(f)}
                            for t, f in zip(g.index, g["pred_gam_forecast"])]}
    A = g["target"].to_numpy(dtype=float)
    log = Path(tempfile.mkdtemp()) / "updates.jsonl"
    for k in range(24):
        at = origin + k * H + WAIT
        up = make(day, k, at, issue_body, A, params, e_lib, doy)
        assert append(up, now=at, log=log)
        nxt = up["hours"][0] if up["hours"] else {"update": issue_body["hours"][0]["official"], "lower": np.nan,
                                                  "upper": np.nan}
        print(f"k={k:2d} ebar {up['ebar']:+.4f}  next hour update {nxt['update']:.0f} "
              f"[{nxt['lower']:.0f}, {nxt['upper']:.0f}] actual {A[k]:.0f}  peak P10/50/90 "
              f"{up['peak']['p10']:.0f}/{up['peak']['p50']:.0f}/{up['peak']['p90']:.0f} at h{up['peak']['hour_ending']}")
    print(f"actual peak {np.nanmax(A):.0f} at h{int(np.nanargmax(A)) + 1}; log {log}")
    return 0


def main(argv) -> int:
    if argv[:1] == ["--freeze"]:
        print(json.dumps(freeze(), indent=1))
        return 0
    if argv[:1] == ["--run"]:
        return run()
    if argv[:1] == ["--rehearse"] and len(argv) == 2:
        return rehearse(argv[1])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
