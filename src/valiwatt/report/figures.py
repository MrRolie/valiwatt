"""Figures for the validation report, one set per language. Every figure has a table twin in the report.

Colours are the first categorical slots of the validated palette, in their fixed order (the first five pass every
colour-vision check as a set; three of them fall below 3:1 against the surface, so every figure keeps its legend and
its table twin); thresholds are neutral dashed lines, never a series colour.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator

from valiwatt.quality.figures import AXIS, GRID, INK, INK_2, SERIES_1, SERIES_2, _num, _save, _style, plt

SERIES_3 = "#1baf7a"
SERIES_4 = "#eda100"
SERIES_5 = "#e87ba4"

LABELS = {
    "fr": {
        "mape": "EAMP mensuelle (%)", "persistence": "v1 tel que livré (météo de la veille)",
        "observed": "météo observée (borne optimiste)", "baseline": "même heure, une semaine avant",
        "gam_forecast": "v2 : le GAM sur la prévision (production)", "forecast": "forme de v1 sur la prévision",
        "effect": "Effet de la température (MW), relatif à 18 °C", "temp": "Prévision de température (°C)",
        "with_lags": "Modèle figé (avec les retards)", "without_lags": "Sans les retards (interprétation seulement)",
        "extrapolated": "extrapolé (hors de la plage d'entraînement)",
        "blocks": {"night": "nuit (0–5 h)", "morning": "matin (6–9 h)", "day": "jour (10–15 h)",
                   "evening": "soir (16–20 h)", "late": "fin de soirée (21–23 h)"},
        "coverage": "Couverture (%)", "nominal": "nominale {} %", "pooled": "quantile unique",
        "by_season": "quantile par saison", "ci": "IC 95 %", "demand": "Demande (MW)", "actual": "réelle",
        "interval": "intervalle à {} %", "week_mape": "EAMP hebdomadaire (%)", "week_bias": "Biais hebdomadaire (%)",
        "week_cov": "Couverture hebdomadaire (%)", "threshold": "seuil de la saison",
        "drift_t": "Dérive, température", "drift_f": "Dérive, prévision", "alarm": "niveau d'alarme",
        "seasons": {"winter": "hiver", "spring": "printemps", "summer": "été", "fall": "automne"},
    },
    "en": {
        "mape": "Monthly MAPE (%)", "persistence": "v1 as shipped (previous day's weather)",
        "observed": "observed weather (optimistic bound)", "baseline": "same hour one week earlier",
        "gam_forecast": "v2: the GAM on the forecast (production)", "forecast": "v1's form on the forecast",
        "effect": "Temperature effect (MW), relative to 18 °C", "temp": "Forecast temperature (°C)",
        "with_lags": "Frozen model (with the lags)", "without_lags": "Without the lags (interpretation only)",
        "extrapolated": "extrapolated (outside the training range)",
        "blocks": {"night": "night (0–5 h)", "morning": "morning (6–9 h)", "day": "day (10–15 h)",
                   "evening": "evening (16–20 h)", "late": "late evening (21–23 h)"},
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


# The forecasts of the monthly figure, in the palette's fixed order: production first, then v1's form on the forecast,
# v1 as shipped and the baseline.
MONTHLY = (("gam_forecast", SERIES_1), ("forecast", SERIES_2), ("persistence", SERIES_3), ("baseline", SERIES_4))


def monthly_mape(monthly: pd.DataFrame, lang: str, path: Path):
    """monthly: index month (YYYY-MM), columns gam_forecast, forecast, persistence, baseline (fractions)."""
    L = LABELS[lang]
    x = pd.PeriodIndex(monthly.index, freq="M").to_timestamp()
    fig, ax = plt.subplots(figsize=(8, 3.4))
    for col, color in MONTHLY:
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
    """The busiest design hours: actual, the three forecasts compared and the production model's interval. A forecast
    a model did not issue for an hour is left out of the figure; the table beside it gives its reason."""
    L = dict(LABELS[lang], interval=LABELS[lang]["interval"].format(_nominal(nominal, lang)))
    fig, ax = plt.subplots(figsize=(7, 3.6))
    y = np.arange(len(hours))
    labels = [pd.Timestamp(h["utc"]).strftime("%Y-%m-%d %H:%M UTC") for h in hours]
    for i, h in enumerate(hours):
        if h["lower"] is not None and h["upper"] is not None:
            ax.plot([h["lower"], h["upper"]], [i, i], color=AXIS, linewidth=5, solid_capstyle="round",
                    label=L["interval"] if i == 0 else None)
    for key, color in (("gam_forecast", SERIES_1), ("forecast", SERIES_2), ("persistence", SERIES_3)):
        pts = [(h.get(f"pred_{key}"), i) for i, h in enumerate(hours)]
        pts = [(x, i) for x, i in pts if x is not None and x == x]
        if pts:
            ax.scatter(*zip(*pts), color=color, s=40, zorder=3, label=L[key], edgecolors="white", linewidths=1.5)
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


# The hour blocks of the partial-effects figure, in the palette's fixed order: v1's blocks of the local day.
BLOCKS = (("night", range(0, 6), SERIES_1), ("morning", range(6, 10), SERIES_2), ("day", range(10, 16), SERIES_3),
          ("evening", range(16, 21), SERIES_4), ("late", range(21, 24), SERIES_5))


def _segments(t: np.ndarray, y: np.ndarray, inside: np.ndarray):
    """The curve cut where it leaves the training range: (x, y, inside) runs; an extrapolated run takes the nearest
    point of the range at each end it shares with it, so the curve stays one line."""
    out, i, n = [], 0, len(t)
    while i < n:
        j = i
        while j < n and inside[j] == inside[i]:
            j += 1
        lo, hi = (i, j) if inside[i] else (max(i - 1, 0), min(j + 1, n))
        out.append((t[lo:hi], y[lo:hi], bool(inside[i])))
        i = j
    return out


def partial_effects(rows: list[dict], lang: str, path: Path):
    """The frozen GAM's 24 temperature curves (plan section 4.5), each relative to its value at 18 °C, beside the same
    curves from the GAM fitted without the lags (interpretation only): solid over the training range, dashed where
    the curve is extrapolated as a straight line. rows: the frozen run's partial_effects.csv (hour, temp_c,
    effect_mw, effect_mw_nolag, in_training_range). Coloured by v1's hour blocks."""
    L = LABELS[lang]
    d = pd.DataFrame(rows).sort_values(["hour", "temp_c"])
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.8))
    for ax, col, title in ((axes[0], "effect_mw", L["with_lags"]), (axes[1], "effect_mw_nolag", L["without_lags"])):
        for block, hours, color in BLOCKS:
            for k, h in enumerate(hours):
                g = d[d["hour"] == h]
                t, y = g["temp_c"].to_numpy(float), g[col].to_numpy(float)
                inside = g["in_training_range"].astype(bool).to_numpy()
                for xs, ys, solid in _segments(t, y, inside):
                    ax.plot(xs, ys, color=color, linewidth=1.0 if solid else 0.9, linestyle="-" if solid else "--",
                            label=(L["blocks"][block] if solid and k == 0 and ax is axes[0] else None))
        ax.axvline(18.0, color=INK_2, linewidth=0.6, linestyle=":")
        ax.set_title(title, fontsize=9, color=INK)
        ax.set_xlabel(L["temp"], fontsize=9)
        ax.xaxis.set_major_formatter(_num(lang, 0))
        ax.yaxis.set_major_formatter(_num(lang, 0))
        _style(ax)
    axes[0].set_ylabel(L["effect"], fontsize=9)
    handles, labels = axes[0].get_legend_handles_labels()
    from matplotlib.lines import Line2D

    handles.append(Line2D([], [], color=INK_2, linestyle="--", linewidth=0.9))
    labels.append(L["extrapolated"])
    fig.legend(handles, labels, frameon=False, fontsize=8, labelcolor=INK, loc="lower center",
               bbox_to_anchor=(0.5, 1.0), ncol=3)
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
