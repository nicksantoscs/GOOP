"""Goop scoring: Whoop-style Recovery (0-100%) and Strain (0-21) from Garmin data.

These are Goop's own transparent heuristics, not Whoop's proprietary algorithms.
"""
from __future__ import annotations

import math
from statistics import mean


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def recovery_score(*, hrv: float | None, hrv_baseline: float | None,
                   rhr: float | None, rhr_baseline: float | None,
                   sleep_score: float | None, body_battery_wake: float | None) -> dict:
    """Weighted blend of four components, each mapped to 0-100.

    - HRV (40%):  last-night HRV vs 7-day baseline. 50 at baseline, +/-25 pts per 10% deviation.
    - RHR (25%):  resting HR vs 7-day baseline. 50 at baseline, +8 pts per bpm below, -8 per bpm above.
    - Sleep (25%): Garmin sleep score (already 0-100).
    - Body Battery at wake (10%): Garmin's 0-100 value.
    Missing components are dropped and the remaining weights re-normalised.
    """
    parts: dict[str, tuple[float, float]] = {}
    if hrv and hrv_baseline:
        parts["hrv"] = (0.40, _clamp(50 + (hrv / hrv_baseline - 1) * 250))
    if rhr and rhr_baseline:
        parts["rhr"] = (0.25, _clamp(50 + (rhr_baseline - rhr) * 8))
    if sleep_score is not None:
        parts["sleep"] = (0.25, _clamp(float(sleep_score)))
    if body_battery_wake is not None:
        parts["body_battery"] = (0.10, _clamp(float(body_battery_wake)))
    if not parts:
        return {"score": None, "zone": None, "components": {}}
    total_w = sum(w for w, _ in parts.values())
    score = round(sum(w * v for w, v in parts.values()) / total_w)
    zone = "green" if score >= 67 else "yellow" if score >= 34 else "red"
    return {
        "score": score,
        "zone": zone,
        "components": {k: {"value": round(v), "weight": round(w / total_w, 2)} for k, (w, v) in parts.items()},
    }


STRAIN_K = 250.0  # load at which strain reaches ~13.3 (1 - 1/e of 21)


def strain_score(*, activity_load: float, moderate_min: float, vigorous_min: float, steps: float) -> dict:
    """Logarithmic 0-21 scale like Whoop: strain = 21 * (1 - e^(-load / 250)).

    load = max(sum of Garmin activityTrainingLoad for the day,
               1.5 * (moderate + 2 * vigorous intensity minutes))
           + 2 per 1,000 steps (background daily movement).
    Diminishing returns: going from 10 -> 14 takes far more work than 0 -> 4.
    """
    effort = max(activity_load or 0.0, 1.5 * ((moderate_min or 0) + 2 * (vigorous_min or 0)))
    load = effort + 2.0 * (steps or 0) / 1000.0
    strain = 21.0 * (1.0 - math.exp(-load / STRAIN_K))
    level = "all out" if strain >= 18 else "strenuous" if strain >= 14 else "moderate" if strain >= 10 else "light"
    return {"score": round(strain, 1), "level": level, "load": round(load, 1)}


def baseline(values: list[float | None]) -> float | None:
    vals = [v for v in values if v]
    return round(mean(vals), 1) if vals else None


def recommended_strain(recovery: int | None) -> str | None:
    if recovery is None:
        return None
    if recovery >= 67:
        return "14-18 · primed, go hard"
    if recovery >= 34:
        return "10-14 · train, but keep it controlled"
    return "0-10 · prioritise recovery today"
