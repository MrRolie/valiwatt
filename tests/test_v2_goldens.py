"""v2's outputs on the synthetic worlds, pinned as literals before v3 touches src/ (plan-v3 step 1, slice 0).

v3 generalizes the clock (a forecast issued at noon the day before); v2's midnight path must come out of that refactor
unchanged to the last bit. These tests hold what v2 produces today on the existing synthetic worlds, captured once at
8561e70 and written as literals in tests/v2_goldens_data.py (never recomputed from the code under test):

- validation (test_validate.py's v2 world): per pred_* column its hours, NaN count, sum and sum of squares; 200
  sampled (hour, value) pairs of the production column; the forecast record; the intervals and the results;
- replay() on world_v2() (five weeks of 2022, clean and with a day no run fed) and on rehearsal_world() (the two
  weeks of 2023 the rehearsal replays): its hourly frame, days, weeks, decisions and forecast record;
- replay() on world_v2() with no runs (v1's path: the `state.forecast is None` branch of issue()), its own frozen
  persistence model as the production model (world_v2's GAM reads the forecast temperature and, with no feed, forecasts
  nothing: every hour NaN);
- each day's issue() frame on those replays, and day_verdicts() with the runs on their days;
- every read of a raw feed (RawFeed.visible and RawFeed.freshest: demand, temperature and the NBM forecast) as
  (feed name, clock time), in the replay and in day_verdicts: the time each feed is read is pinned, so a refactor of
  the clock that reads the forecast feed an hour before or after the origin fails here even where the runs it then
  chooses, and so every output, come out the same;
- the frozen models' hashes (FrozenModel.sha256, freeze.frozen_sha256), the sha256 of their JSON, and the sums and sums
  of squares of their stored values (coefficients, knots, scaler means and scales) and their intercept.

A digest is the sha256 of a canonical text of the frame or records (tests/golden.py): it matches only when every cell
is the same to the last bit, so it catches any drift; the sampled values and sums say what moved, to 1e-9. check()
compares every value first and every digest and sha256 after them, so a drift reports the value that moved before any
hash that moved with it; a hash that fails after every value passed moved below 1e-9 (the last bits).

On CI (GitHub Actions sets CI=true) the digests and sha256 of floats are not compared: CI's OpenBLAS rounds the last
bits differently, so they cannot match the literals captured here. Every value is still compared to 1e-9 there, and the
feed-read digests (text and times, no float) are compared everywhere.
"""

import hashlib
import os

import numpy as np
import pandas as pd
import pytest

import replay_world as rw
from golden import column_stats, frame_digest, records_digest, sample
from test_frozen import frozen_gam, frozen_ols, oos, world  # noqa: F401 (fixtures)
from test_validate import v2  # noqa: F401 (fixture)
from v2_goldens_data import GOLDEN
from valiwatt.monitor import replay as rp

REL = 1e-9
# CI's OpenBLAS differs from this machine's in the last bits of a float: on CI (GitHub Actions sets CI=true) the exact
# digests and sha256 (EXACT_KEYS) are skipped, every value still compared to REL. Locally everything is compared.
EXACT = not os.environ.get("CI")
EXACT_KEYS = ("digest", "sha256")  # a key holding one of these is a hash, compared exactly, after every value
FLOAT_FREE = ("feed_reads", "verdict_feed_reads")  # hashes of feed names and clock times only: compared on CI too
FEED_READS_SHOWN = 5  # the first and last reads written out as literals beside each read list's digest
REPLAY_SPAN = ("2022-01-03", "2022-02-06")  # test_replay_v2's five Monday-to-Sunday weeks of world_v2()
UNFED_DAY = pd.Timestamp("2022-01-19")  # test_replay_v2's day no run fed


def _unfed_runs(runs: pd.DataFrame) -> pd.DataFrame:
    """test_replay_v2's runs with every run in view at UNFED_DAY's origin missing its last bracket point."""
    o, h = rp.origin(UNFED_DAY), pd.Timedelta(hours=1)
    return runs[~((runs["runtime"] <= o - 2 * h) & (runs["ftime"] == o + 25 * h))].reset_index(drop=True)


def _recording_feed_reads(mp, reads: list) -> None:
    """Every read of any raw feed (RawFeed.visible, RawFeed.freshest) appended to `reads` as (feed name, clock time)
    at the moment of the call. RawFeed.window reads through visible, so it is recorded there."""
    for name in ("visible", "freshest"):
        real = getattr(rp.RawFeed, name)

        def read(self, *args, _real=real, **kwargs):
            reads.append((self.name, self._clock.now.isoformat()))
            return _real(self, *args, **kwargs)

        mp.setattr(rp.RawFeed, name, read)


def _replay_with_issues(w, model, first, last, data_end, runs, comparators):
    """replay() on world `w`, every issue() frame it produced kept in order, every feed read recorded."""
    issued, reads = [], []
    real = rp.issue

    def keep(*args, **kwargs):
        out = real(*args, **kwargs)
        issued.append(out.copy())
        return out

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(rp, "issue", keep)
        _recording_feed_reads(mp, reads)
        r = rp.replay(w.raw, w.obs, model, w.thresholds, w.reference, first, last, data_end, runs=runs,
                      comparators=comparators)
    return r, issued, reads


def _replay_case(w, first, last, data_end, runs, model=None):
    """A replay and day_verdicts on world `w`; with no runs (v1's path) no comparison model is scored."""
    model = w.model if model is None else model
    comparators = None if runs is None else w.comparators
    r, issued, reads = _replay_with_issues(w, model, first, last, data_end, runs, comparators)
    verdict_reads = []
    with pytest.MonkeyPatch.context() as mp:
        _recording_feed_reads(mp, verdict_reads)
        verdicts = rp.day_verdicts(w.raw, w.obs, pd.Timestamp(first), pd.Timestamp(last), runs=runs)
    return r, issued, verdicts, reads, verdict_reads


@pytest.fixture(scope="module")
def replays():
    w = rw.world_v2()
    rh = rw.rehearsal_world()
    return {
        "world_v2": _replay_case(w, *REPLAY_SPAN, rw.LAST, w.runs),
        "world_v2_unfed": _replay_case(w, *REPLAY_SPAN, rw.LAST, _unfed_runs(w.runs)),
        "world_v2_no_runs": _replay_case(w, *REPLAY_SPAN, rw.LAST, None, model=w.comparators["persistence"]),
        "rehearsal": _replay_case(rh, *rw.CHAIN_DAYS[2023], rw.REHEARSAL_LAST, rh.runs),
    }


# ------------------------------------------------------------------------------------------------- observations

def observe_validation(v2) -> dict:  # noqa: F811
    out = v2.out
    preds = sorted(c for c in out.columns if c.startswith("pred_"))
    return {
        "out_digest": frame_digest(out),
        "out_columns": list(out.columns),
        "stats": {c: column_stats(out[c]) for c in preds},
        "production_sample": sample(out["pred_gam_forecast"], 200),
        "record_digest": {k: frame_digest(x) for k, x in sorted(v2.record.items())},
        "returned_record_digest": frame_digest(v2.v["forecast_record"]),
        "record_fed_days": {k: int((x["reason"] == "").sum()) for k, x in sorted(v2.record.items())},
        "intervals_digest": frame_digest(v2.v["intervals"]),
        "intervals_persistence_digest": frame_digest(v2.v["intervals_persistence"]),
        "half_width_stats": column_stats((v2.v["intervals"]["upper"] - v2.v["intervals"]["lower"]) / 2),
        "covered_mean": float(v2.v["intervals"]["covered"].mean()),
        "coverage": {k: v2.r["coverage"][k]["relative"]["overall"]["mean"]
                     for k in ("gam_forecast", "persistence", "gam_observed", "observed")},
        "results_digest": records_digest(v2.r),
        "keys": sorted(v2.v),
    }


def observe_reads(reads: list) -> dict:
    n = FEED_READS_SHOWN
    return {"n": len(reads), "digest": records_digest(reads), "first": reads[:n], "last": reads[-n:]}


def observe_replay(case) -> dict:
    r, issued, verdicts, reads, verdict_reads = case
    return {
        "hourly_digest": frame_digest(r.hourly),
        "hourly_columns": list(r.hourly.columns),
        "days_digest": records_digest(r.days),
        "weeks_digest": records_digest(r.weeks),
        "decisions_digest": records_digest(r.decisions),
        "record_digest": None if r.forecast_record is None else frame_digest(r.forecast_record),
        "temperature_reference_digest": None if r.temperature_reference is None else
        frame_digest(r.temperature_reference.to_frame()),
        "drift_samples_digest": records_digest(r.drift_samples),
        "model_sha256": r.model_sha256,
        "comparators_sha256": dict(sorted(r.comparators_sha256.items())),
        "clock_end": r.clock_end.isoformat(),
        "issue_days": len(issued),
        "issue_digest": frame_digest(pd.concat(issued)),
        "verdicts_digest": records_digest(verdicts),
        "failed_days": sorted(d.date().isoformat() for d, p in verdicts.items() if p),
        "recommendations": [(d["week_start"].date().isoformat(), d["recommendation"]) for d in r.decisions],
        "forecast_stats": column_stats(r.hourly["forecast"]),
        "forecast_sample": sample(r.hourly["forecast"], 12),
        "ape_stats": column_stats(r.hourly["ape"]),
        "feed_reads": observe_reads(reads),
        "verdict_feed_reads": observe_reads(verdict_reads),
    }


def _array_stats(values) -> dict:
    a = np.asarray(values, dtype=float).ravel()
    return {"n": int(a.size), "sum": float(a.sum()), "sumsq": float((a ** 2).sum())}


def _model_entry(m) -> dict:
    return {"sha256": m.sha256(), "json_sha256": hashlib.sha256(m.to_json().encode()).hexdigest(), "q": float(m.q),
            **{k: _array_stats(getattr(m, k)) for k in ("beta", "knots", "center", "scale")},
            "intercept": float(m.intercept)}


def observe_frozen(frozen_gam, frozen_ols) -> dict:  # noqa: F811
    rh, w = rw.rehearsal_world(), rw.world_v2()
    return {
        "test_frozen": {"gam_forecast": _model_entry(frozen_gam[0]),
                        **{k: _model_entry(m) for k, m in sorted(frozen_ols.items())}},
        "world_v2": {"gam_forecast": _model_entry(w.model),
                     **{k: _model_entry(m) for k, m in sorted(w.comparators.items())}},
        "rehearsal": {"gam_forecast": _model_entry(rh.model),
                      **{k: _model_entry(m) for k, m in sorted(rh.comparators.items())}},
    }


# ---------------------------------------------------------------------------------------------------- the checks

def _exact(key) -> bool:
    return any(k in str(key) for k in EXACT_KEYS)


def _walk(got, want, hashes: bool, path=(), in_hash=False, float_free=False):
    """One pass over the expected structure. hashes False: everything outside a hash key (structure, text, ints,
    floats to REL relative or REL absolute near zero). hashes True: only what sits under a hash key, exactly; on CI
    (not EXACT) only the float-free ones (FLOAT_FREE)."""
    if isinstance(want, dict):
        assert sorted(got) == sorted(want), path
        for k in want:
            h = in_hash or _exact(k)
            ff = float_free or k in FLOAT_FREE
            if hashes and h and not (EXACT or ff):
                continue  # CI: a hash of floats, rounded differently there
            _walk(got[k], want[k], hashes, path + (k,), h, ff)
    elif isinstance(want, (list, tuple)):
        assert len(got) == len(want), path
        for i, (g, x) in enumerate(zip(got, want)):
            _walk(g, x, hashes, path + (i,), in_hash, float_free)
    elif in_hash != hashes:
        return  # the other pass compares it
    elif isinstance(want, float):
        assert got == pytest.approx(want, rel=REL, abs=REL), path
    else:
        assert got == want, path


def check(got, want):
    """Every value first, at every depth, then every digest and sha256: a hash that fails after every sampled value
    and sum passed moved below 1e-9 (the last bits); a value that moved is reported before any hash can hide it."""
    _walk(got, want, hashes=False)
    _walk(got, want, hashes=True)


def test_v2_validation_is_unchanged(v2):  # noqa: F811
    check(observe_validation(v2), GOLDEN["validation"])


@pytest.mark.parametrize("name", ["world_v2", "world_v2_unfed", "world_v2_no_runs", "rehearsal"])
def test_v2_replay_issue_and_day_verdicts_are_unchanged(replays, name):
    check(observe_replay(replays[name]), GOLDEN["replay"][name])


def test_v2_frozen_model_hashes_are_unchanged(frozen_gam, frozen_ols):  # noqa: F811
    check(observe_frozen(frozen_gam, frozen_ols), GOLDEN["frozen"])
