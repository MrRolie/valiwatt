"""Render the validation report (French first, English derived) into docs/report/, from the chain of tracked runs.

The report is committed (owner's ruling). It carries a stamp (docs/report/stamp.json): the run IDs it was rendered
from, the code hash, the rules hash and the hash of every rendered file. `tests/test_report_stamp.py` fails when the
committed code no longer matches the stamp, or a rendered file no longer matches its hash, so a report can never sit
beside code that did not produce it, nor be edited by hand. The data-quality report is rendered from the chain's own
data-quality run.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import pandas as pd

from valiwatt import attribution
from valiwatt.paths import REPO_ROOT, REPORTS
from valiwatt.report import figures as F
from valiwatt.report.lineage import Chain, follow
from valiwatt.report.render import ReportRefused, assert_public_safe, environment, render

OUT = REPORTS
STAMP = OUT / "stamp.json"
LANGS = ("fr", "en")


def _json(c, run, path: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        return json.loads(Path(c.download_artifacts(run.info.run_id, path, tmp)).read_text(encoding="utf-8"))


def _frame(c, run, path: str) -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(c.download_artifacts(run.info.run_id, path, tmp))
        return pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)


def _when(run) -> str:
    return pd.Timestamp(run.info.start_time, unit="ms", tz="UTC").strftime("%Y-%m-%d %H:%M UTC")


def season_coverage(coverage: dict, nominal: float) -> list[dict]:
    """Coverage by season for the production interval (one quantile) and the per-season sensitivity, each with its
    week-block 95 % interval. For each season: whether the per-season interval contains the nominal, and whether the
    two forms' intervals are disjoint. No interval on the difference between the forms is computed, so disjoint
    intervals are all the comparison shows. Both judgements use the bounds as the tables print them (one decimal of a
    percentage), so a sentence never contradicts the numbers beside it."""
    rows = []
    for season in F.SEASON_ORDER:
        row = {"key": season}
        for form, name in (("pooled", "relative"), ("by_season", "relative_by_season")):
            found = [x for x in coverage[name]["season"] if x["key"] == season]
            if len(found) != 1:
                raise ReportRefused(f"the validation run has {len(found)} {name} coverage rows for {season}")
            x = found[0]
            row[form] = {"covered": x["covered"], "low": x["covered_low"], "high": x["covered_high"]}
        one, per = ({k: round(v, 3) for k, v in row[f].items()} for f in ("pooled", "by_season"))
        row["contains_nominal"] = bool(per["low"] <= round(nominal, 3) <= per["high"])
        row["disjoint"] = bool(one["high"] < per["low"] or per["high"] < one["low"])
        rows.append(row)
    return rows


def context(chain: Chain, code_sha: str, rules_sha: str) -> dict:
    from valiwatt.tracking import client

    c = client()
    v = _json(c, chain.validation, "results/validation.json")
    dq = _json(c, chain.dq, "results/dq_results.json")
    oos = _frame(c, chain.validation, "frames/oos.parquet")
    monthly = pd.DataFrame({k: ((oos["target"] - oos[col]).abs() / oos["target"]).groupby(oos["fold"]).mean()
                            for k, col in (("persistence", "pred_persistence"), ("observed", "pred_observed"),
                                           ("baseline", "baseline"))})
    frozen = {}
    for year, run in chain.frozen.items():
        frozen[year] = {"run_id": run.info.run_id, "params": run.data.params, "tags": run.data.tags,
                        "coefficients": _frame(c, run, "coefficients.csv").to_dict("records")}
    return {
        "v": v, "dq": dq, "p": _json(c, chain.pseudo, "results/replay.json"),
        "r": _json(c, chain.replay, "results/replay.json"), "b": _json(c, chain.breaks, "results/breaks.json"),
        "frozen": frozen, "monthly": monthly,
        "seasons": season_coverage(v["coverage"], 1 - v["conformal"]["alpha"]),
        "departures": [{"id": run.info.run_id, "freeze_commit": run.data.tags.get("freeze_commit", ""),
                        "summary": _json(c, run, "results/replay.json")["summary"]} for run in chain.departed],
        "runs": {k: {"id": run.info.run_id, "when": _when(run)} for k, run in chain.runs().items()},
        "code_sha": code_sha, "rules_sha": rules_sha, "freeze": chain.freeze,
        "commit": chain.replay.data.tags.get("git_commit", "none"),
        "dirty": chain.replay.data.tags.get("git_dirty") != "false",
    }


def figures(ctx: dict, lang: str) -> dict:
    out = {}
    v, p, r = ctx["v"], ctx["p"], ctx["r"]
    jobs = {
        "validation-monthly-mape": lambda path: F.monthly_mape(ctx["monthly"], lang, path),
        "validation-coverage-by-season": lambda path: F.coverage_by_season(
            {form: {x["key"]: (x[form]["covered"], x[form]["low"], x[form]["high"]) for x in ctx["seasons"]}
             for form in ("pooled", "by_season")}, lang, path),
        "validation-peak-hours": lambda path: F.peak_hours(v["cases"]["peak"]["hours"], lang, path),
        "pseudo-replay-2023-weekly": lambda path: F.replay_weekly(p["weeks"], p["decisions"], lang, path),
        "replay-2024-weekly": lambda path: F.replay_weekly(r["weeks"], r["decisions"], lang, path),
        "replay-2024-drift": lambda path: F.replay_drift(r["weeks"], lang, path),
    }
    for name, job in jobs.items():
        path = OUT / "figures" / "validation" / f"{name}.{lang}.png"
        job(path)
        out[name.replace("-", "_")] = f"figures/validation/{path.name}"
    return out


def file_hashes() -> dict:
    """The sha256 of every rendered file under docs/report/, the stamp itself excepted."""
    return {p.relative_to(OUT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(OUT.rglob("*")) if p.is_file() and p != STAMP}


def render_validation(dq_run_id: str | None = None) -> list[Path]:
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.monitor.run import check_freeze
    from valiwatt.tracking import code_sha256

    code_sha, rules_sha = code_sha256(), rules_sha256()
    chain = follow(code_sha, rules_sha, check_freeze())
    if dq_run_id and dq_run_id != chain.dq.info.run_id:
        raise ReportRefused(f"data-quality run {dq_run_id} is not the one the chain read ({chain.dq.info.run_id})")
    written = render(run_id=chain.dq.info.run_id)
    ctx = context(chain, code_sha, rules_sha)
    if (OUT / "figures" / "validation").exists():  # no figure from an earlier render survives into the stamp
        shutil.rmtree(OUT / "figures" / "validation")
    retrieved = min(s["first_retrieved_utc"] for s in ctx["dq"]["sources"]["eccc"])[:10]
    for lang in LANGS:
        figs = figures(ctx, lang)
        text = environment(lang).get_template(f"validation.{lang}.md.j2").render(
            **ctx, fig=figs, lang=lang, attribution=attribution.lines(lang, retrieved=retrieved))
        assert_public_safe(text)
        out = OUT / f"validation.{lang}.md"
        out.write_text(text, encoding="utf-8")
        written.append(out)
    stamp = {"code_sha256": code_sha, "rules_sha256": rules_sha, "freeze_commit": chain.freeze["freeze_commit"],
             "runs": {k: x["id"] for k, x in ctx["runs"].items()}, "files": file_hashes()}
    STAMP.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    print("rendered " + ", ".join(p.relative_to(REPO_ROOT).as_posix() for p in written)
          + f" from replay run {chain.replay.info.run_id}")
    return written
