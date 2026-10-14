"""Figures for the validation report, one set per language. Every figure has a table twin in the report.

Colours are the first three categorical slots of the validated palette (they pass every colour-vision check as a
set); thresholds are neutral dashed lines, never a series colour.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator

from valiwatt.quality.figures import AXIS, GRID, INK, INK_2, SERIES_1, SERIES_2, _num, _save, _style, plt

SERIES_3 = "#1baf7a"

LABELS = {
    "fr": {
        "mape": "EAMP mensuelle (%)", "persistence": "météo de la veille (production)",
        "observed": "météo observée (borne optimiste)", "baseline": "même heure, une semaine avant",
        "coverage": "Couverture (%)", "nominal": "nominale {} %", "pooled": "quantile unique",
        "by_season": "quantile par saison", "ci": "IC 95 %", "demand": "Demande (MW)", "actual": "réelle",
        "interval": "intervalle à {} %", "week_mape": "EAMP hebdomadaire (%)", "week_bias": "Biais hebdomadaire (%)",
        "week_cov": "Couverture hebdomadaire (%)", "threshold": "seuil de la saison",
        "drift_t": "Dérive, température", "drift_f": "Dérive, prévision", "alarm": "niveau d'alarme",
        "seasons": {"winter": "hiver", "spring": "printemps", "summer": "été", "fall": "automne"},
    },
    "en": {
        "mape": "Monthly MAPE (%)", "persistence": "previous day's weather (production)",
        "observed": "observed weather (optimistic bound)", "baseline": "same hour one week earlier",
        "coverage": "Coverage (%)", "nominal": "nominal {}%", "pooled": "one quantile",
        "by_season": "one quantile per season", "ci": "95% CI", "demand": "Demand (MW)", "actual": "actual",
        "interval": "{}% interval", "week_mape": "Weekly MAPE (%)", "week_bias": "Weekly bias (%)",
        "week_cov": "Weekly coverage (%)", "threshold": "season's threshold",
        "drift_t": "Drift, temperature", "drift_f": "Drift, forecast", "alarm": "alarm level",
        "seasons": {"winter": "winter", "spring": "spring", "summer": "summer", "fall": "fall"},
    },
}
SEASON_ORDER = ("winter", "spring", "summer", "fall")


def _legend(ax):
    leg = ax.legend(frameon=False, fontsize=8, labelcolor=INK)
    return leg


def monthly_mape(monthly: pd.DataFrame, lang: str, path: Path):
    """monthly: index month (YYYY-MM), columns persistence, observed, baseline (fractions)."""
    L = LABELS[lang]
    x = pd.PeriodIndex(monthly.index, freq="M").to_timestamp()
    fig, ax = plt.subplots(figsize=(8, 3.4))
    for col, color in (("baseline", SERIES_2), ("persistence", SERIES_1), ("observed", SERIES_3)):
        ax.plot(x, 100 * monthly[col], color=color, linewidth=1.6, marker="o", markersize=3, label=L[col])
    ax.set_ylabel(L["mape"], fontsize=9)
    ax.yaxis.set_major_formatter(_num(lang, 0))
    ax.set_ylim(bottom=0)
    _style(ax)
    _legend(ax)
    _save(fig, path)


def _nominal(nominal: float, lang: str) -> str:
    return _num(lang, 0)(100 * nominal, None)


def coverage_by_season(rows: dict, lang: str, path: Path, nominal: float):
    """rows: {"pooled": {season: (coverage, ci_low, ci_high)}, "by_season": {...}}; the 95 % interval is drawn as a
    thin error bar, the point label sits above it; the dashed line is the intervals' nominal coverage."""
    L = dict(LABELS[lang], nominal=LABELS[lang]["nominal"].format(_nominal(nominal, lang)))
    fig, ax = plt.subplots(figsize=(6, 3.2))
    x = np.arange(len(SEASON_ORDER))
    w = 0.36
    top = 0.0
    for i, (key, color) in enumerate((("pooled", SERIES_1), ("by_season", SERIES_2))):
        vals, lows, highs = (np.array([100 * rows[key][s][j] for s in SEASON_ORDER]) for j in range(3))
        xs = x + (i - 0.5) * (w + 0.02)
        ax.bar(xs, vals, width=w, color=color, label=L[key])
        ax.errorbar(xs, vals, yerr=[vals - lows, highs - vals], fmt="none", ecolor=INK_2, elinewidth=0.8, capsize=2,
                    capthick=0.8, label=L["ci"] if i == 0 else None)
        for xi, v, hi in zip(xs, vals, highs):
            ax.text(xi, hi + 0.6, _num(lang, 1)(v, None), ha="center", fontsize=7, color=INK)
        top = max(top, highs.max())
    ax.axhline(100 * nominal, color=INK_2, linewidth=0.9, linestyle="--", label=L["nominal"])
    ax.set_xticks(x, [L["seasons"][s] for s in SEASON_ORDER])
    ax.set_ylim(60, max(102, top + 3))
    ax.set_ylabel(L["coverage"], fontsize=9)
    ax.yaxis.set_major_formatter(_num(lang, 0))
    _style(ax)
    handles, labels = ax.get_legend_handles_labels()
    order = [labels.index(L[k]) for k in ("pooled", "by_season", "ci", "nominal")]
    ax.legend([handles[i] for i in order], [labels[i] for i in order], frameon=False, fontsize=8, labelcolor=INK,
              loc="lower left", bbox_to_anchor=(0, 1.0), ncol=4)
    _save(fig, path)


def peak_hours(hours: list[dict], lang: str, path: Path, nominal: float):
    """The busiest design hours: actual, both forecasts and the interval."""
    L = dict(LABELS[lang], interval=LABELS[lang]["interval"].format(_nominal(nominal, lang)))
    fig, ax = plt.subplots(figsize=(7, 3.4))
    y = np.arange(len(hours))
    labels = [pd.Timestamp(h["utc"]).strftime("%Y-%m-%d %H:%M UTC") for h in hours]
    for i, h in enumerate(hours):
        if h["lower"] is not None and h["upper"] is not None:
            ax.plot([h["lower"], h["upper"]], [i, i], color=AXIS, linewidth=5, solid_capstyle="round",
                    label=L["interval"] if i == 0 else None)
    ax.scatter([h["pred_persistence"] for h in hours], y, color=SERIES_1, s=40, zorder=3, label=L["persistence"],
               edgecolors="white", linewidths=1.5)
    ax.scatter([h["pred_observed"] for h in hours], y, color=SERIES_3, s=40, zorder=3, label=L["observed"],
               edgecolors="white", linewidths=1.5)
    ax.scatter([h["target"] for h in hours], y, color=INK, marker="D", s=36, zorder=4, label=L["actual"],
               edgecolors="white", linewidths=1.5)
    ax.set_yticks(y, labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel(L["demand"], fontsize=9)
    ax.xaxis.set_major_formatter(_num(lang, 0))
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    _style(ax)
    ax.grid(axis="y", visible=False)
    ax.legend(frameon=False, fontsize=8, labelcolor=INK, loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2)
    _save(fig, path)


def replay_weekly(weeks: list[dict], decisions: list[dict], lang: str, path: Path):
    """Three small multiples: weekly MAPE, bias and coverage against the week's season thresholds."""
    L = LABELS[lang]
    ev = [w for w in weeks if w["evaluated"]]
    x = [pd.Timestamp(w["week_start"]) for w in ev]
    fig, axes = plt.subplots(3, 1, figsize=(8, 7), sharex=True)
    panels = (
        ("mape", L["week_mape"], [("mape_high",)]),
        ("rel_bias", L["week_bias"], [("bias_low",), ("bias_high",)]),
        ("covered", L["week_cov"], [("coverage_low",)]),
    )
    flagged = {pd.Timestamp(d["week_start"]) for d in decisions if d["recommendation"]}
    for ax, (stat, label, lines) in zip(axes, panels):
        ax.plot(x, [100 * w["stats"][stat] for w in ev], color=SERIES_1, linewidth=1.4, marker="o", markersize=3)
        for (key,) in lines:
            ax.step(x, [100 * w["thresholds"][key] for w in ev], where="mid", color=INK_2, linewidth=0.9,
                    linestyle="--", label=L["threshold"] if key in ("mape_high", "bias_low", "coverage_low") else None)
        hit = [(xi, 100 * w["stats"][stat]) for xi, w in zip(x, ev) if xi in flagged]
        if hit:
            ax.scatter(*zip(*hit), s=70, facecolors="none", edgecolors=INK, linewidths=1.4, zorder=4)
        ax.set_ylabel(label, fontsize=9)
        ax.yaxis.set_major_formatter(_num(lang, 1))
        _style(ax)
    axes[0].legend(frameon=False, fontsize=8, labelcolor=INK)
    _save(fig, path)


def replay_drift(weeks: list[dict], lang: str, path: Path):
    L = LABELS[lang]
    x = [pd.Timestamp(w["week_start"]) for w in weeks]
    fig, axes = plt.subplots(2, 1, figsize=(8, 4.6), sharex=True)
    for ax, (key, label) in zip(axes, (("temperature", L["drift_t"]), ("forecast", L["drift_f"]))):
        ax.plot(x, [w["drift"][key] for w in weeks], color=SERIES_1, linewidth=1.4, marker="o", markersize=3)
        ax.axhline(weeks[0]["drift_thresholds"][key], color=INK_2, linewidth=0.9, linestyle="--", label=L["alarm"])
        ax.set_ylabel(label, fontsize=9)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=6, steps=[1, 2, 5, 10]))  # ticks a one-decimal label can name
        ax.yaxis.set_major_formatter(_num(lang, 1))
        ax.set_ylim(bottom=0)
        _style(ax)
    axes[0].legend(frameon=False, fontsize=8, labelcolor=INK)
    _save(fig, path)
