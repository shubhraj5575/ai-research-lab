"""Dependency-free SVG figure generation from real experimental data.

Every function takes values that came from the runs/analyses tables and emits
a self-contained SVG file. No matplotlib, no network fonts, no external
assets. Output is byte-for-byte deterministic for identical inputs.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Sequence

_PALETTE = [
    "#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed",
    "#0891b2", "#be185d", "#4b5563",
]

_W, _H = 760, 440
_ML, _MR, _MT, _MB = 64, 24, 34, 52  # margins


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _color(label: str, index: int) -> str:
    return _PALETTE[index % len(_PALETTE)]


def _nice_ticks(lo: float, hi: float, target: int = 6) -> list[float]:
    """Human-friendly tick locations covering [lo, hi]."""
    if hi <= lo:
        hi = lo + 1.0
    span = hi - lo
    raw_step = span / max(1, target)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    for mult in (1, 2, 2.5, 5, 10):
        step = mult * magnitude
        if step >= raw_step:
            break
    start = math.ceil(lo / step) * step
    ticks = []
    value = start
    while value <= hi + 1e-12:
        ticks.append(round(value, 10))
        value += step
    return ticks


def _fmt(value: float) -> str:
    if abs(value) >= 1000 or (value != 0 and abs(value) < 0.01):
        return f"{value:.1e}"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.4g}"


def line_chart(series: dict[str, Sequence[float]], title: str,
               x_label: str, y_label: str, out_path: Path,
               x_scale: float = 1.0) -> Path:
    """Multi-series line chart; x positions are indices scaled by ``x_scale``."""
    plot_w = _W - _ML - _MR
    plot_h = _H - _MT - _MB
    all_values = [v for vals in series.values() for v in vals]
    if not all_values:
        raise ValueError("no data for line chart")
    vmax_all = max(all_values)
    vmin_all = min(0.0, min(all_values))
    pad = (vmax_all - vmin_all) * 0.05 or 1.0
    lo, hi = vmin_all - pad * 0.2, vmax_all + pad
    ticks_y = _nice_ticks(lo, hi)

    def xy(i: int, v: float, n: int) -> tuple[float, float]:
        px = _ML + (plot_w * i / max(1, n - 1))
        py = _MT + plot_h - plot_h * (v - lo) / (hi - lo)
        return px, py

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{_H}" '
        f'viewBox="0 0 {_W} {_H}">',
        f'<rect width="{_W}" height="{_H}" fill="#ffffff"/>',
        f'<text x="{_ML}" y="22" font-family="Helvetica,Arial" '
        f'font-size="15" fill="#111827">{_esc(title)}</text>',
    ]
    # y grid + labels
    for t in ticks_y:
        _, py = xy(0, t, 2)
        if _MT - 4 <= py <= _MT + plot_h + 4:
            parts.append(
                f'<line x1="{_ML}" y1="{py:.1f}" x2="{_W - _MR}" y2="{py:.1f}" '
                f'stroke="#e5e7eb" stroke-width="1"/>'
            )
            parts.append(
                f'<text x="{_ML - 8}" y="{py + 4:.1f}" text-anchor="end" '
                f'font-family="Helvetica,Arial" font-size="11" fill="#6b7280">'
                f'{_fmt(t)}</text>'
            )
    # axes
    parts.append(
        f'<line x1="{_ML}" y1="{_MT + plot_h}" x2="{_W - _MR}" '
        f'y2="{_MT + plot_h}" stroke="#9ca3af"/>'
    )
    parts.append(
        f'<line x1="{_ML}" y1="{_MT}" x2="{_ML}" y2="{_MT + plot_h}" stroke="#9ca3af"/>'
    )
    # x ticks (5 evenly spaced indices)
    n_points = max(len(v) for v in series.values())
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        idx = int(frac * (n_points - 1))
        px, py = xy(idx, lo, n_points)
        label_x = idx * x_scale
        parts.append(
            f'<text x="{px:.1f}" y="{_MT + plot_h + 18}" text-anchor="middle" '
            f'font-family="Helvetica,Arial" font-size="11" fill="#6b7280">'
            f'{_fmt(label_x)}</text>'
        )
    parts.append(
        f'<text x="{_ML + plot_w / 2}" y="{_H - 14}" text-anchor="middle" '
        f'font-family="Helvetica,Arial" font-size="12" fill="#374151">'
        f'{_esc(x_label)}</text>'
    )
    parts.append(
        f'<text x="16" y="{_MT + plot_h / 2}" transform="rotate(-90 16 {_MT + plot_h / 2})" '
        f'text-anchor="middle" font-family="Helvetica,Arial" font-size="12" '
        f'fill="#374151">{_esc(y_label)}</text>'
    )
    # series
    legend_items = []
    for i, (label, values) in enumerate(series.items()):
        color = _color(label, i)
        n = len(values)
        pts = " ".join(
            f"{xy(j, v, n)[0]:.1f},{xy(j, v, n)[1]:.1f}"
            for j, v in enumerate(values)
        )
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="2"/>'
        )
        legend_items.append((label, color))
    # legend
    lx = _ML + 10
    ly = _MT + 16
    for j, (label, color) in enumerate(legend_items):
        yy = ly + j * 18
        parts.append(f'<rect x="{lx}" y="{yy - 9}" width="14" height="4" fill="{color}"/>')
        parts.append(
            f'<text x="{lx + 20}" y="{yy - 1}" font-family="Helvetica,Arial" '
            f'font-size="11" fill="#374151">{_esc(label[:44])}</text>'
        )
    parts.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


def comparison_bars(items: Sequence[tuple[str, float, float, float]],
                    title: str, y_label: str, out_path: Path,
                    lower_is_better: bool = True) -> Path:
    """Horizontal bars with CI whiskers: items = [(label, mean, lo, hi)]."""
    row_h = 34
    plot_w = _W - _ML - _MR
    height = _MT + _MB + row_h * len(items)
    values = [m for _, m, _, _ in items] + [lo for _, _, lo, _ in items] + \
             [hi for _, _, _, hi in items]
    lo_v, hi_v = min(values), max(values)
    pad = (hi_v - lo_v) * 0.08 or 1.0
    lo_v -= pad
    hi_v += pad

    def xv(v: float) -> float:
        return _ML + plot_w * (v - lo_v) / (hi_v - lo_v)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{height}" '
        f'viewBox="0 0 {_W} {height}">',
        f'<rect width="{_W}" height="{height}" fill="#ffffff"/>',
        f'<text x="{_ML}" y="22" font-family="Helvetica,Arial" font-size="15" '
        f'fill="#111827">{_esc(title)}</text>',
    ]
    baseline_x = _ML  # left edge = best value under direction
    for i, (label, mean, lo_ci, hi_ci) in enumerate(items):
        y = _MT + i * row_h
        color = _color(label, i)
        bar_w = max(2.0, xv(mean) - _ML)
        parts.append(
            f'<rect x="{_ML}" y="{y}" width="{bar_w:.1f}" height="{row_h - 12}" '
            f'fill="{color}" opacity="0.85"/>'
        )
        # CI whisker
        cy = y + (row_h - 12) / 2
        parts.append(
            f'<line x1="{xv(lo_ci):.1f}" y1="{cy:.1f}" x2="{xv(hi_ci):.1f}" '
            f'y2="{cy:.1f}" stroke="#111827" stroke-width="2"/>'
        )
        for bound in (lo_ci, hi_ci):
            bx = xv(bound)
            parts.append(
                f'<line x1="{bx:.1f}" y1="{cy - 5:.1f}" x2="{bx:.1f}" '
                f'y2="{cy + 5:.1f}" stroke="#111827" stroke-width="2"/>'
            )
        parts.append(
            f'<text x="{xv(mean) + 8:.1f}" y="{cy + 4:.1f}" '
            f'font-family="Helvetica,Arial" font-size="11" fill="#111827">'
            f'{_fmt(mean)}  [{_fmt(lo_ci)}, {_fmt(hi_ci)}]</text>'
        )
        parts.append(
            f'<text x="{_ML - 8}" y="{cy + 4:.1f}" text-anchor="end" '
            f'font-family="Helvetica,Arial" font-size="11" fill="#374151">'
            f'{_esc(label[:36])}</text>'
        )
    parts.append(
        f'<text x="{_W - _MR}" y="{height - 10}" text-anchor="end" '
        f'font-family="Helvetica,Arial" font-size="11" fill="#6b7280">'
        f'{_esc(y_label)}{" (lower is better)" if lower_is_better else ""}'
        f'; whiskers = bootstrap 95% CI</text>'
    )
    parts.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path


def outcome_timeline(rows: Sequence[tuple[int, str]], title: str,
                     out_path: Path) -> Path:
    """One cell per iteration: rows = [(iteration, status_string)]."""
    cell = 46
    width = _ML + cell * len(rows) + _MR
    height = 120
    colors = {
        "supported": "#059669", "refuted": "#dc2626", "inconclusive": "#d97706",
        "testing": "#2563eb", "superseded": "#9ca3af",
    }
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{_ML}" y="22" font-family="Helvetica,Arial" font-size="15" '
        f'fill="#111827">{_esc(title)}</text>',
    ]
    for i, (it, status) in enumerate(rows):
        x = _ML + i * cell
        y = 44
        color = colors.get(status.lower(), "#9ca3af")
        parts.append(
            f'<rect x="{x}" y="{y}" width="{cell - 8}" height="{cell - 8}" rx="6" '
            f'fill="{color}" opacity="0.9"/>'
        )
        short = status[:4]
        parts.append(
            f'<text x="{x + (cell - 8) / 2}" y="{y + 22}" text-anchor="middle" '
            f'font-family="Helvetica,Arial" font-size="11" fill="#ffffff">{short}</text>'
        )
        parts.append(
            f'<text x="{x + (cell - 8) / 2}" y="{y + cell + 2}" text-anchor="middle" '
            f'font-family="Helvetica,Arial" font-size="10" fill="#6b7280">{it}</text>'
        )
    parts.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return out_path
