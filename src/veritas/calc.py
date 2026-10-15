"""Deterministic financial arithmetic. The LLM never does maths; it cites these results."""
from __future__ import annotations


def pct_change(new: float, old: float) -> float:
    if old == 0:
        raise ValueError("base value is zero")
    return (new / old - 1) * 100


def margin_pct(numerator: float, revenue: float) -> float:
    if revenue == 0:
        raise ValueError("revenue is zero")
    return numerator / revenue * 100


def bps_change(new_pct: float, old_pct: float) -> float:
    return (new_pct - old_pct) * 100


def pledge_of_total(pledged_pct_of_promoter: float, promoter_pct_of_total: float) -> float:
    """Convert 'pledged % of promoter holding' to '% of total shares'."""
    return pledged_pct_of_promoter / 100 * promoter_pct_of_total


def pct_change_within_rounding(new: float, old: float, reported_pct: float, half_unit: float = 0.5) -> bool:
    """Is a reported growth % attainable given inputs rounded to the nearest unit?"""
    lo = pct_change(new - half_unit, old + half_unit)
    hi = pct_change(new + half_unit, old - half_unit)
    return lo - 0.05 <= reported_pct <= hi + 0.05


def spent_and_remaining(total: float, pct_spent: float) -> tuple[float, float]:
    spent = total * pct_spent / 100
    return spent, total - spent


def implied_upside(target: float, price: float) -> float:
    return pct_change(target, price)


def normalise_number(s: str) -> float:
    """'₹1,428' / '1,428 crore' / '11.2%' -> float."""
    cleaned = "".join(ch for ch in s if ch.isdigit() or ch in ".-")
    if cleaned in ("", "-", "."):
        raise ValueError(f"no number in {s!r}")
    return float(cleaned)
