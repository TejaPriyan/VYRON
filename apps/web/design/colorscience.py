"""Colour maths for the design-token checks. Standard library only.

* WCAG 2.x relative luminance and contrast ratio.
* OKLab (Ottosson, 2020) and Delta E as Euclidean distance x100.
* Colour-vision-deficiency simulation with the Machado, Oliveira & Fernandes (2009) matrices at
  severity 1.0, applied in linear sRGB.

These are published formulas re-implemented here so the checks run without Node or any package.
`tests/test_design_contract.py` pins this implementation to numbers produced by the separate dataviz
validator (see PINNED_REFERENCE_VALUES there), so a typo in a matrix would be caught.
"""

from __future__ import annotations

import math
import re
from itertools import combinations

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

_MACHADO = {
    "protan": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deutan": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritan": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}

Vec = tuple[float, float, float]


def parse_hex(value: str) -> tuple[float, float, float]:
    """``#rrggbb`` -> sRGB components in 0..1. Anything else raises ``ValueError``."""
    if not _HEX.fullmatch(value):
        raise ValueError(f"not a #rrggbb colour: {value!r}")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_rgb(value: str) -> Vec:
    r, g, b = parse_hex(value)
    return (_to_linear(r), _to_linear(g), _to_linear(b))


def relative_luminance(value: str) -> float:
    r, g, b = linear_rgb(value)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 2.x contrast ratio, 1..21, symmetric."""
    hi, lo = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _oklab_from_linear(rgb: Vec) -> Vec:
    r, g, b = rgb
    l_ = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m_ = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s_ = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def oklab(value: str) -> Vec:
    return _oklab_from_linear(linear_rgb(value))


def oklch(value: str) -> tuple[float, float, float]:
    """Lightness, chroma, hue in degrees."""
    lightness, a, b = oklab(value)
    return lightness, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def _simulate(rgb: Vec, kind: str) -> Vec:
    matrix = _MACHADO[kind]
    out = [sum(row[i] * rgb[i] for i in range(3)) for row in matrix]
    return (min(1.0, max(0.0, out[0])), min(1.0, max(0.0, out[1])), min(1.0, max(0.0, out[2])))


def delta_e(a: str, b: str, vision: str | None = None) -> float:
    """OKLab distance x100. ``vision`` is None (normal), "protan", "deutan" or "tritan"."""
    if vision is not None and vision not in _MACHADO:
        raise ValueError(f"unknown vision type: {vision!r}")
    la, lb = linear_rgb(a), linear_rgb(b)
    if vision:
        la, lb = _simulate(la, vision), _simulate(lb, vision)
    return 100 * math.dist(_oklab_from_linear(la), _oklab_from_linear(lb))


def worst_pair(colors: list[str], vision: str | None = None) -> tuple[float, str, str]:
    """Smallest Delta E over all pairs (the check a node-link graph needs)."""
    if len(colors) < 2:
        raise ValueError("need at least two colours")
    return min((delta_e(a, b, vision), a, b) for a, b in combinations(colors, 2))


def worst_cvd_pair(colors: list[str]) -> tuple[float, str, str, str]:
    """Smallest Delta E over all pairs under protan and deutan vision."""
    best: tuple[float, str, str, str] | None = None
    for vision in ("protan", "deutan"):
        d, a, b = worst_pair(colors, vision)
        if best is None or d < best[0]:
            best = (d, vision, a, b)
    assert best is not None
    return best
