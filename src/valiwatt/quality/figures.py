"""Figures for the data-quality report, one set per language. Every figure has a table twin in the report."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
AXIS = "#b9b8b3"
SERIES_1 = "#2a78d6"
SERIES_2 = "#eb6834"

LABELS = {
    "fr": {
        "daily_mw": "Demande moyenne journalière (MW)",
        "temp_c": "Température moyenne journalière, {centre} (°C)",
        "r2": "R² de la demande journalière (degrés-jours et jour de la semaine)",
        "resid": "Écart au modèle de 2019 (%)",
        "panel": "panel (moyenne)",
    },
    "en": {
        "daily_mw": "Daily mean demand (MW)",
        "temp_c": "Daily mean temperature, {centre} (°C)",
        "r2": "R² of daily demand (degree-days and day of week)",
        "resid": "Gap to the 2019 model (%)",
        "panel": "panel (mean)",
    },
}


def _style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(AXIS)
    ax.tick_params(colors=INK_2, labelsize=8)
    ax.yaxis.label.set_color(INK)
    ax.xaxis.label.set_color(INK)
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _num(lang: str, digits: int = 0):
    def fmt(x, _):
        s = f"{x:,.{digits}f}"
        if lang == "fr":
            s = s.replace(",", "\u202f").replace(".", ",")
        return s.replace("-", "\u2212")

    return matplotlib.ticker.FuncFormatter(fmt)


def _save(fig, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def daily_demand(daily: pd.DataFrame, lang: str, path: Path):
    fig, ax = plt.subplots(figsize=(8, 3.2))
    ax.plot(daily.index, daily["demand_mw"], color=SERIES_1, linewidth=0.9)
    ax.set_ylabel(LABELS[lang]["daily_mw"], fontsize=9)
    ax.yaxis.set_major_formatter(_num(lang))
    _style(ax)
    _save(fig, path)


def heating_curve(daily: pd.DataFrame, source: str, centre: str, lang: str, path: Path):
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(daily[source], daily["demand_mw"], s=6, alpha=0.45, color=SERIES_1, edgecolors="none")
    ax.set_xlabel(LABELS[lang]["temp_c"].format(centre=centre), fontsize=9)
    ax.xaxis.set_major_formatter(_num(lang))
    ax.set_ylabel(LABELS[lang]["daily_mw"], fontsize=9)
    ax.yaxis.set_major_formatter(_num(lang))
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    _style(ax)
    _save(fig, path)


def r2_by_source(rows: list[dict], names: dict[str, str], lang: str, path: Path):
    rows = sorted(rows, key=lambda r: r["r2"])
    labels = [names.get(r["source"], LABELS[lang]["panel"]) for r in rows]
    colors = [SERIES_2 if r["source"] == "panel" else SERIES_1 for r in rows]
    fig, ax = plt.subplots(figsize=(6, 0.42 * len(rows) + 0.6))
    ax.barh(labels, [r["r2"] for r in rows], color=colors, height=0.6)
    lo = min(r["r2"] for r in rows)
    ax.set_xlim(max(0.0, lo - 0.1), 1.0)
    for i, r in enumerate(rows):
        text = f"{r['r2']:.3f}".replace(".", ",") if lang == "fr" else f"{r['r2']:.3f}"
        ax.text(r["r2"] + 0.004, i, text, va="center", fontsize=8, color=INK)
    ax.set_xlabel(LABELS[lang]["r2"], fontsize=9)
    ax.xaxis.set_major_formatter(_num(lang, 3))
    ax.grid(axis="x", color=GRID, linewidth=0.6)
    ax.grid(axis="y", visible=False)
    _style(ax)
    ax.grid(axis="y", visible=False)
    _save(fig, path)


def residual_by_month(resid: pd.DataFrame, lang: str, path: Path):
    fig, ax = plt.subplots(figsize=(8, 3.2))
    x = pd.PeriodIndex(resid.index, freq="M").to_timestamp()
    ax.bar(x, resid["mean_residual_pct"], width=20, color=SERIES_1)
    ax.axhline(0, color=INK_2, linewidth=0.8)
    ax.set_ylabel(LABELS[lang]["resid"], fontsize=9)
    ax.yaxis.set_major_formatter(_num(lang))
    _style(ax)
    _save(fig, path)
