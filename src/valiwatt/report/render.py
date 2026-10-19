"""Render the data-quality report (French first, English derived) from a tracked MLflow run."""

from __future__ import annotations

import functools
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
from valiwatt.tracking import client, code_sha256, latest_run

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

    def holds(interval, x, d=1):
        """Whether a 95 % interval ({"ci95_low", "ci95_high"}) holds x, judged on the bounds as pct(d) prints them, so
        the sentence never contradicts its numbers."""
        def shown(v):
            return float(f"{100 * v:.{d}f}")
        return shown(interval["ci95_low"]) <= shown(x) <= shown(interval["ci95_high"])

    def mw(values, present=True, d=1):
        """A revised record's raw values as the data-quality run lists them (quality/run.py `revised_records`: a
        label's values joined by ";", a blank record empty), each formatted on its own, a blank record named; no
        record at the label (`present` false): —."""
        if not present:
            return "\u2014"
        blank, sep = {"fr": ("vide", " ; "), "en": ("blank", "; ")}[lang]
        return sep.join(fmt_num(float(v), d, lang) if v.strip() else blank for v in str(values).split(";"))

    def sci(x, d=1):
        """A small positive number in scientific form, its mantissa as `num` prints it: 1.1 × 10⁻¹¹ (1,1 × 10⁻¹¹ in
        French); refused when it is not positive, which the sentence then states another way."""
        if x is None or not x > 0:
            raise ReportRefused(f"a number the report prints in scientific form is not positive: {x!r}")
        mantissa, exponent = f"{x:.{d}e}".split("e")
        return f"{fmt_num(float(mantissa), d, lang)} \u00d7 10" + str(int(exponent)).translate(SUPERSCRIPT)

    return {"num": num, "pct": pct, "signed": signed, "utc": utc, "lst": lst, "day": day, "verdict": verdict,
            "short": short, "reason": reason, "holds": holds, "mw": mw, "sci": sci}


SUPERSCRIPT = str.maketrans("-0123456789", "\u207b\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079")


def bootstrap_quantiles(func) -> tuple[float, float]:
    """The two quantiles of its bootstrap draws that `func` takes as its interval, read from its source: the one
    `np.quantile(draws, [low, high])` call it makes, its bounds symmetric. Refused otherwise, so a change to the code
    that computes an interval cannot leave the reports stating another level."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "quantile" and len(n.args) == 2]
    if len(calls) != 1:
        raise ReportRefused(f"{func.__qualname__} does not take its interval by one np.quantile call")
    low, high = (float(x) for x in ast.literal_eval(calls[0].args[1]))
    if not 0 < low < high < 1 or abs(low - (1 - high)) > 1e-12:
        raise ReportRefused(f"{func.__qualname__} takes the quantiles {low} and {high}: not a symmetric interval")
    return low, high


@functools.cache
def design() -> dict:
    """The design constants the templates state that no run carries, read from the code that applies them: the level
    of the week-block bootstrap intervals (forecast/evaluate.py `mean_with_ci`, every interval of the validation
    report) and of the data-quality report's R² interval (quality/representativeness.py `r2_difference`); the
    temperature at which the frozen GAM's curves are zero (forecast/freeze.py `BASE_C`); the offset of local standard
    time from UTC, in hours (config `LST_OFFSET`); the rise in demand whose carry the lags section states
    (report/soundness.py `CARRY_RISE_MW`); the hours after its issue time a temperature forecast is taken as available
    (sources/nbm.py `AVAILABILITY_LAG`). Templates read them as the global `design`."""
    from valiwatt.config import LST_OFFSET
    from valiwatt.forecast import evaluate, freeze
    from valiwatt.quality import representativeness
    from valiwatt.report import soundness
    from valiwatt.sources import nbm

    def level(func):
        low, high = bootstrap_quantiles(func)
        return round(high - low, 9)

    return {"ci_level": level(evaluate.mean_with_ci), "ci_quantiles": bootstrap_quantiles(evaluate.mean_with_ci),
            "dq_ci_level": level(representativeness.r2_difference), "ref_c": float(freeze.BASE_C),
            "utc_offset_h": LST_OFFSET / pd.Timedelta(hours=1), "carry_rise_mw": soundness.CARRY_RISE_MW,
            "availability_lag_h": nbm.AVAILABILITY_LAG / pd.Timedelta(hours=1)}


def environment(lang: str) -> jinja2.Environment:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), undefined=jinja2.StrictUndefined,
                             trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    env.filters.update(filters(lang))
    env.globals["design"] = design()
    return env


def assert_public_safe(text: str) -> None:
    """A rendered report never carries a local path."""
    leaks = [p for p in (str(REPO_ROOT), "/home/", "/tmp/", "file://") if p in text]
    if leaks or re.search(r"\b[A-Za-z]:\\", text):
        raise ReportRefused(f"rendered report contains a local path: {leaks}")


def assert_attributed(text: str, lang: str) -> None:
    """Every public page carries the NOAA and IEM attribution and the widened no-endorsement line, in the owner's
    approved wording (attribution.py, held to AGENTS.md by a test)."""
    missing = [name for name, line in (("NBM", attribution.NBM[lang]), ("NO_ENDORSEMENT",
                                                                        attribution.NO_ENDORSEMENT[lang]))
               if line not in text]
    if missing:
        raise ReportRefused(f"the rendered page lacks the attribution lines {missing} (NOAA and the IEM)")


# The words of the finding a lead of 75 hours or more makes against the two-day argument of plan section 9.2: the
# reports must state it (they refuse to render otherwise), and this is their one home.
LEAD_FINDING = {"fr": "constat contre l'argument des deux jours (section 9.2 du plan)",
                "en": "finding against the two-day argument (plan section 9.2)"}


def nbm_findings(dq: dict) -> dict:
    """The forecast's data-quality gates and finding of a data-quality run's results (plan section 3.4): its
    coverage, the days no run fed with why, the leads, and the years whose longest lead reaches the bound of plan
    section 9.2's argument. A run without them (made before the forecast was checked) is refused."""
    checks = dq.get("checks", {})
    if any(k not in checks for k in ("nbm_bounds", "nbm_coverage", "nbm_runs")):
        raise ReportRefused(f"data-quality run {dq.get('run_id')} holds no forecast checks; run `valiwatt dq` again")
    runs = checks["nbm_runs"]
    m = runs["metrics"]
    not_fed = [x for x in runs.get("evidence", []) if "day" in x and "reason" in x]
    return {"bounds": checks["nbm_bounds"], "coverage": checks["nbm_coverage"], "runs": m, "not_fed": not_fed,
            "reused": [x for x in runs.get("evidence", []) if "reused_day" in x],
            "longest": {int(y): float(h) for y, h in m["longest_lead_h"].items()},
            "at_or_above": sorted(int(y) for y in m["leads_at_or_above_reuse_bound"])}


def local_year(hour_ending_utc) -> int:
    """The year, in local standard time, of the hour that ends at `hour_ending_utc`: an hour belongs to the day it
    starts in (the data-quality report's time conventions), so the window ending 2025-01-01 05:00 UTC ends in 2024."""
    from valiwatt.config import LST_OFFSET

    return (pd.Timestamp(hour_ending_utc) - pd.Timedelta(hours=1) + LST_OFFSET).year


def nbm_source(results: dict) -> dict:
    """The NBM forecast's row of the data-quality report's sources table, from the fetch manifest the run carries
    (`sources.nbm`): its station and product, its yearly files and when they were fetched (the first and last day). A
    run without it (made before the forecast was a source) is refused."""
    src = (results.get("sources") or {}).get("nbm")
    years = (src or {}).get("years") or {}
    if not years or any(not (y or {}).get("retrieved_utc") for y in years.values()):
        raise ReportRefused(f"data-quality run {results.get('run_id')} holds no NBM fetch manifest; run `valiwatt dq` "
                            "again")
    days = sorted(str(y["retrieved_utc"])[:10] for y in years.values())
    return {"station": src.get("station"), "model": src.get("model"), "files": len(years), "first_retrieved": days[0],
            "last_retrieved": days[-1], "rows": sum(int(y.get("rows") or 0) for y in years.values())}


def dq_context(results: dict, run) -> dict:
    """What the data-quality report reads: the run's results, its provenance and its forecast findings. The years it
    states are those before the held-out year: a run of the 2025 vintage leaves 2025's findings to the validation
    report, from the run its test of record read (plan section 13)."""
    from valiwatt.config import EVALUATION, HELD_OUT_YEAR
    from valiwatt.sources.nbm import MAX_RUNS_BACK

    nbm = nbm_findings(results)
    window = results["checks"]["demand_window"]["metrics"]
    return {
        "window_years": (local_year(window["first_utc"]), local_year(window["last_utc"])),
        "seen_year": next(y for y, x in EVALUATION.items() if x == "seen_by_developers"),
        "nbm_source": nbm_source(results),
        "r": results,
        "run_id": run.info.run_id,
        "commit": run.data.tags.get("git_commit", "none"),
        "dirty": run.data.tags.get("git_dirty") != "false",
        "code_sha": run.data.tags.get("code_sha256", "unknown"),
        "run_date": pd.Timestamp(run.info.start_time, unit="ms", tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "nbm": nbm,
        "shown_at_or_above": [y for y in nbm["at_or_above"] if y < HELD_OUT_YEAR],
        "lead_finding": LEAD_FINDING,
        "max_runs_back": MAX_RUNS_BACK,
        "held_out_year": HELD_OUT_YEAR,
        "reaches_held_out": reaches_held_out(run),
    }


def forecast_section(text: str) -> str:
    """A rendered data-quality page's section 4.1, on the temperature forecast, up to the next second-level heading;
    "" when the page has none."""
    at = text.find("\n### 4.1 ")
    if at < 0:
        return ""
    end = text.find("\n## ", at + 1)
    return text[at:] if end < 0 else text[at:end]


def check_dq(text: str, lang: str, context: dict) -> None:
    """The statements a rendered data-quality page must carry: the attribution lines, and, in its section on the
    forecast (4.1), the finding against plan section 9.2's argument for every year it states whose longest lead
    reaches the bound: « En bref » says it too, and must not stand in for it."""
    assert_attributed(text, lang)
    if context["shown_at_or_above"] and LEAD_FINDING[lang] not in forecast_section(text):
        raise ReportRefused("a lead at or above the bound of plan section 9.2 is not stated in section 4.1")


def reaches_held_out(run) -> bool:
    """Whether a data-quality run's window reaches past the end of 2024 (its param `window_last_utc`): a run of the
    2025 vintage. A run without the param (made before windows had an end of their own) ends with 2024."""
    from valiwatt.config import data_last

    last = run.data.params.get("window_last_utc")
    return bool(last) and pd.Timestamp(last) > data_last(2024)


def refuse_held_out_vintage(c, run) -> None:
    """Before any 2025 replay has started (docs/plan-v2.md, section 11.3.4), nothing of the 2025 vintage is rendered:
    neither the data-quality run asked for, named or found by default, when it reaches 2025, nor any run while the
    latest data-quality run does. The 2025 starts are read (report/ledger.py `starts`) only when such a run is in view,
    since nothing is refused otherwise."""
    from valiwatt.report import ledger

    if not reaches_held_out(run):
        try:
            latest = latest_run(EXPERIMENT, "tags.kind = 'dq'")
        except LookupError:
            return
        if not reaches_held_out(latest):
            return
        culprit = latest
    else:
        culprit = run
    if ledger.starts(c, 2025):
        return
    raise ReportRefused(f"data-quality run {culprit.info.run_id} reaches 2025, and no 2025 replay has started: "
                        "nothing of the 2025 vintage is rendered before the first 2025 start (plan section 11.3.4)")


def render(run_id: str | None = None) -> list[Path]:
    c = client()
    run = c.get_run(run_id) if run_id else latest_run(
        EXPERIMENT, "tags.kind = 'dq' and tags.gates = 'passed' and attributes.status = 'FINISHED'")
    if run.data.tags.get("gates") != "passed" or run.info.status != "FINISHED":
        raise ReportRefused(f"run {run.info.run_id} did not pass its gates; no report is rendered from it")
    refuse_held_out_vintage(c, run)
    # The provenance line names the run's code; the templates and filters render at report time. They must be the same.
    if run.data.tags.get("code_sha256") != code_sha256():
        raise ReportRefused(f"the code changed since run {run.info.run_id}; run `valiwatt dq` again before rendering")
    with tempfile.TemporaryDirectory() as tmp:
        results = json.loads(Path(c.download_artifacts(run.info.run_id, "results/dq_results.json", tmp)).read_text())
        figs = Path(c.download_artifacts(run.info.run_id, "figures", tmp))
        REPORTS.mkdir(parents=True, exist_ok=True)
        for lang in LANGS:  # only this report's figure folders: the validation report keeps its own
            if (REPORTS / "figures" / lang).exists():
                shutil.rmtree(REPORTS / "figures" / lang)
            shutil.copytree(figs / lang, REPORTS / "figures" / lang)

    context = dq_context(results, run)
    written = []
    for lang in LANGS:
        retrieved = min(s["first_retrieved_utc"] for s in results["sources"]["eccc"])[:10]
        text = environment(lang).get_template(f"dq.{lang}.md.j2").render(
            **context, attribution=attribution.lines(lang, retrieved=retrieved), lang=lang)
        assert_public_safe(text)
        check_dq(text, lang, context)
        out = REPORTS / f"dq.{lang}.md"
        out.write_text(text, encoding="utf-8")
        c.log_artifact(run.info.run_id, str(out), "report")
        written.append(out)
    print("rendered " + ", ".join(f"docs/report/{p.name}" for p in written) + f" from run {run.info.run_id}")
    return written
