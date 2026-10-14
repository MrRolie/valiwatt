"""Render the validation report (French first, English derived) into docs/report/, from the chain of tracked runs.

The report is committed (owner's ruling). It carries a stamp (docs/report/stamp.json): the run IDs it was rendered
from, the code hash and the rules hash. `tests/test_report_stamp.py` fails when the committed code no longer matches
the stamp, so a report can never sit beside code that did not produce it.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd

from valiwatt import attribution
from valiwatt.paths import REPO_ROOT, REPORTS
from valiwatt.report import figures as F
from valiwatt.report.lineage import Chain, follow
from valiwatt.report.render import assert_public_safe, environment

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
            {"pooled": {x["key"]: x["covered"] for x in v["coverage"]["relative"]["season"]},
             "by_season": {x["key"]: x["covered"] for x in v["coverage"]["relative_by_season"]["season"]}}, lang, path),
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


def render_validation() -> list[Path]:
    from valiwatt.monitor.rules import rules_sha256
    from valiwatt.monitor.run import check_freeze
    from valiwatt.tracking import code_sha256

    code_sha, rules_sha = code_sha256(), rules_sha256()
    chain = follow(code_sha, rules_sha, check_freeze())
    ctx = context(chain, code_sha, rules_sha)
    retrieved = min(s["first_retrieved_utc"] for s in ctx["dq"]["sources"]["eccc"])[:10]
    written = []
    for lang in LANGS:
        figs = figures(ctx, lang)
        text = environment(lang).get_template(f"validation.{lang}.md.j2").render(
            **ctx, fig=figs, lang=lang, attribution=attribution.lines(lang, retrieved=retrieved))
        assert_public_safe(text)
        out = OUT / f"validation.{lang}.md"
        out.write_text(text, encoding="utf-8")
        written.append(out)
    stamp = {"code_sha256": code_sha, "rules_sha256": rules_sha, "freeze_commit": chain.freeze["freeze_commit"],
             "runs": {k: x["id"] for k, x in ctx["runs"].items()}}
    STAMP.write_text(json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
    print("rendered " + ", ".join(p.relative_to(REPO_ROOT).as_posix() for p in written)
          + f" from replay run {chain.replay.info.run_id}")
    return written
