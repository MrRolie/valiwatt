"""Render the data-quality report (French first, English derived) from a tracked MLflow run."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

import jinja2
import pandas as pd

from valiwatt import attribution
from valiwatt.paths import REPO_ROOT, REPORTS
from valiwatt.quality.run import EXPERIMENT, LANGS
from valiwatt.tracking import client, latest_run

TEMPLATES = Path(__file__).parent / "templates"


class ReportRefused(RuntimeError):
    pass


def fmt_num(x, digits: int = 0, lang: str = "fr") -> str:
    if x is None:
        raise ReportRefused("a number the report needs is missing from the run")
    s = f"{x:,.{digits}f}"
    if lang == "fr":
        s = s.replace(",", "\u202f").replace(".", ",")
    if s.startswith("-"):
        # A true minus sign; a value that rounds to zero loses its sign.
        s = "\u2212" + s[1:] if s.strip("-0,.\u202f") else s[1:]
    return s


def filters(lang: str) -> dict:
    def num(x, d=0):
        return fmt_num(x, d, lang)

    def pct(x, d=1):
        s = fmt_num(100 * x, d, lang)
        return f"{s} %" if lang == "fr" else f"{s}%"

    def signed(x, d=1):
        s = fmt_num(abs(x), d, lang)
        if not s.strip("0,.\u202f"):
            return s
        return ("+" if x > 0 else "\u2212") + s

    def utc(ts):
        return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M") + " UTC"

    def lst(ts):
        return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M") + (" HNE" if lang == "fr" else " EST")

    def day(ts):
        return pd.Timestamp(ts).strftime("%Y-%m-%d")

    def reason(station):
        return station["reason_fr"] if lang == "fr" else station["reason"]

    def verdict(passed):
        return {"fr": ("réussi", "ÉCHEC"), "en": ("passed", "FAILED")}[lang][0 if passed else 1]

    def short(sha):
        return sha[:12]

    return {"num": num, "pct": pct, "signed": signed, "utc": utc, "lst": lst, "day": day, "verdict": verdict,
            "short": short, "reason": reason}


def environment(lang: str) -> jinja2.Environment:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), undefined=jinja2.StrictUndefined,
                             trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    env.filters.update(filters(lang))
    return env


def assert_public_safe(text: str) -> None:
    """A rendered report never carries a local path."""
    leaks = [p for p in (str(REPO_ROOT), "/home/", "/tmp/", "file://") if p in text]
    if leaks or re.search(r"\b[A-Za-z]:\\", text):
        raise ReportRefused(f"rendered report contains a local path: {leaks}")


def render(run_id: str | None = None) -> list[Path]:
    c = client()
    run = c.get_run(run_id) if run_id else latest_run(
        EXPERIMENT, "tags.kind = 'dq' and tags.gates = 'passed' and attributes.status = 'FINISHED'")
    if run.data.tags.get("gates") != "passed" or run.info.status != "FINISHED":
        raise ReportRefused(f"run {run.info.run_id} did not pass its gates; no report is rendered from it")
    with tempfile.TemporaryDirectory() as tmp:
        results = json.loads(Path(c.download_artifacts(run.info.run_id, "results/dq_results.json", tmp)).read_text())
        figs = Path(c.download_artifacts(run.info.run_id, "figures", tmp))
        REPORTS.mkdir(parents=True, exist_ok=True)
        if (REPORTS / "figures").exists():
            shutil.rmtree(REPORTS / "figures")
        shutil.copytree(figs, REPORTS / "figures")

    context = {
        "r": results,
        "run_id": run.info.run_id,
        "commit": run.data.tags.get("git_commit", "none"),
        "dirty": run.data.tags.get("git_dirty") != "false",
        "code_sha": run.data.tags.get("code_sha256", "unknown"),
        "run_date": pd.Timestamp(run.info.start_time, unit="ms", tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
    }
    written = []
    for lang in LANGS:
        retrieved = min(s["first_retrieved_utc"] for s in results["sources"]["eccc"])[:10]
        text = environment(lang).get_template(f"dq.{lang}.md.j2").render(
            **context, attribution=attribution.lines(lang, retrieved=retrieved), lang=lang)
        assert_public_safe(text)
        out = REPORTS / f"dq.{lang}.md"
        out.write_text(text, encoding="utf-8")
        c.log_artifact(run.info.run_id, str(out), "report")
        written.append(out)
    print("rendered " + ", ".join(f"reports/{p.name}" for p in written) + f" from run {run.info.run_id}")
    return written
