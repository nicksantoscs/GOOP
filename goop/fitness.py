"""Health tab: Fitness Age, pace of aging and contributing factors.

Live mode: the headline is ONLY Garmin's native Fitness Age (fitnessage-service `fitnessAge`); if Garmin
has none, the headline is empty (no Goop estimate). Factor rows prefer Garmin's own `components`
(rhr, bmi/bodyFat, vigorousMinutesAvg, vigorousDaysAvg, with targetValue/potentialAge) and fall back to
the day data. Demo mode uses labelled sample data. Factor "years" are Goop heuristics (table below).
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import mean, pstdev


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def vo2_norm(age: float, sex: str | None) -> float:
    """Rough population-average VO2 max (ml/kg/min) for age/sex (ACSM-style, simplified)."""
    base = 44.0 if sex != "female" else 37.0
    return base - 0.35 * max(0.0, age - 25)


# Each factor: how to read it, what "optimal"/"sufficient" mean, and how many years it adds (+) or removes (-).
FACTORS = [
    {
        "key": "vo2max", "label": "VO\u2082 Max", "unit": "ml/kg/min", "nd": 1,
        "optimal": lambda v, c: v >= vo2_norm(c["age"], c["sex"]) + 5,
        "sufficient": lambda v, c: v >= vo2_norm(c["age"], c["sex"]) - 2,
        "range": lambda c: f"≥ {vo2_norm(c['age'], c['sex']) + 5:.0f}",
        "years": lambda v, c: _clamp(-(v - vo2_norm(c["age"], c["sex"])) * 0.3, -5, 5),
    },
    {
        "key": "rhr", "label": "Resting Heart Rate", "unit": "bpm", "nd": 0,
        "optimal": lambda v, c: v <= 60, "sufficient": lambda v, c: v <= 70,
        "range": lambda c: "≤ 60",
        "years": lambda v, c: _clamp((v - 60) * 0.2, -2.5, 4),
    },
    {
        "key": "body", "label": "BMI", "unit": "", "nd": 1,  # relabelled to Body Fat when that's what we have
        "optimal": lambda v, c: 18.5 <= v < 25, "sufficient": lambda v, c: 17.5 <= v < 27.5,
        "range": lambda c: "18.5 – 24.9",
        "years": lambda v, c: 0.0 if 18.5 <= v < 25 else _clamp((v - 25) * 0.6 if v >= 25 else (18.5 - v) * 0.6, 0, 4),
    },
    {
        "key": "vigorous", "label": "Vigorous Minutes (weekly)", "unit": "min", "nd": 0,
        "optimal": lambda v, c: v >= 75, "sufficient": lambda v, c: v >= 40,
        "range": lambda c: "≥ 75 / week",
        "years": lambda v, c: _clamp(-(v - 75) / 75 * 1.5 if v >= 75 else (75 - v) / 75 * 2.5, -2, 2.5),
    },
    {
        "key": "sleep_consistency", "label": "Sleep Consistency", "unit": "min", "nd": 0,  # SD of bedtime
        "optimal": lambda v, c: v <= 30, "sufficient": lambda v, c: v <= 60,
        "range": lambda c: "bedtime ± 30 min",
        "years": lambda v, c: _clamp((v - 30) / 30 * 0.8, -0.5, 2),
        "display": lambda v: f"± {v:.0f} min",
    },
    {
        "key": "steps", "label": "Steps (daily avg)", "unit": "", "nd": 0,
        "optimal": lambda v, c: v >= 8000, "sufficient": lambda v, c: v >= 5000,
        "range": lambda c: "≥ 8,000",
        "years": lambda v, c: _clamp((8000 - v) / 4000, -1.0, 2.0),
        "display": lambda v: f"{v:,.0f}",
    },
]

# Body-fat variant of the "body" factor (used when Garmin has a body-fat reading)
BODY_FAT = {
    "label": "Body Fat", "unit": "%", "nd": 1,
    "opt": {"male": (8, 20), "female": (18, 30), None: (10, 25)},
}


def _bodyfat_factor(sex):
    lo, hi = BODY_FAT["opt"].get(sex, BODY_FAT["opt"][None])
    return {
        "key": "body", "label": BODY_FAT["label"], "unit": "%", "nd": 1,
        "optimal": lambda v, c: lo <= v <= hi, "sufficient": lambda v, c: lo - 3 <= v <= hi + 5,
        "range": lambda c: f"{lo} – {hi}%",
        "years": lambda v, c: 0.0 if lo <= v <= hi else _clamp((v - hi) * 0.3 if v > hi else (lo - v) * 0.3, 0, 4),
    }


def _bed_minutes(day):
    s = (day.get("sleep") or {}).get("start_local")
    if not s or len(s) < 16:
        return None
    m = int(s[11:13]) * 60 + int(s[14:16])
    return m + 1440 if m < 12 * 60 else m  # put post-midnight bedtimes after evening ones


MIN_POINTS, MIN_SPAN_DAYS, EMA_ALPHA = 6, 56, 0.3


def _median(v):
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _enough(pts) -> bool:
    """>= 6 weekly points spanning >= 8 weeks, with at least one change in Fitness Age."""
    return (len(pts) >= MIN_POINTS and (pts[-1][0] - pts[0][0]).days >= MIN_SPAN_DAYS
            and len({round(v, 2) for _, v in pts}) > 1)


def _raw_pace(pts) -> float:
    """1 + Theil-Sen slope (Fitness Age years per calendar year) of a 3-point rolling median."""
    ys = [v for _, v in pts]
    sm = [_median(ys[max(0, i - 1):i + 2]) for i in range(len(ys))]
    xs = [(d - pts[0][0]).days / 365.25 for d, _ in pts]
    slopes = [(sm[j] - sm[i]) / (xs[j] - xs[i]) for i in range(len(xs)) for j in range(i + 1, len(xs)) if xs[j] > xs[i]]
    return 1 + _median(slopes)


def pace_series(history: list[list]) -> list[tuple[str, float]]:
    """Weekly pace of aging: for each week with enough history, the raw pace from all points up to that week,
    EMA-smoothed (alpha 0.3) across weeks and clamped to [-1, 3]. 1.0x = Fitness Age moving at calendar speed."""
    pts = [(date.fromisoformat(d), float(v)) for d, v in history if v is not None]
    out, ema = [], None
    for k in range(len(pts)):
        sub = pts[:k + 1]
        if not _enough(sub):
            continue
        raw = _raw_pace(sub)
        ema = raw if ema is None else EMA_ALPHA * raw + (1 - EMA_ALPHA) * ema
        out.append((sub[-1][0].isoformat(), round(_clamp(ema, -1.0, 3.0), 2)))
    return out


def _pace(history: list[list]) -> float | None:
    s = pace_series(history)
    return s[-1][1] if s else None


def build(raw: dict, days: list[dict], target: date, mode: str) -> dict:
    """raw: {'age','sex','garmin_fitness_age','achievable','components','bmi','body_fat','vo2max','history'}.
    days: recent normalised day dicts (oldest -> newest, last = target)."""
    age, sex = raw.get("age"), raw.get("sex")
    last7, last14 = days[-7:], days[-14:]
    comps = raw.get("components") or {}

    def comp(*names):
        for n in names:
            v = (comps.get(n) or {}).get("value") if isinstance(comps.get(n), dict) else None
            if v is not None:
                return v
        return None

    # Garmin component extras (target + potential age gain), keyed by our factor key
    def extra(name):
        c = comps.get(name) if isinstance(comps.get(name), dict) else None
        if not c:
            return None
        fa_now = raw.get("garmin_fitness_age")
        gain = round(c["potentialAge"] - fa_now, 1) if (c.get("potentialAge") is not None and fa_now is not None) else None
        return {"target": c.get("targetValue"), "potential_gain": gain, "stale": c.get("stale")}

    vals = {
        "vo2max": raw.get("vo2max") or next((d["vo2max"] for d in reversed(days) if d.get("vo2max")), None),
        "rhr": comp("rhr") or (mean([d["rhr"] for d in last7 if d.get("rhr")]) if any(d.get("rhr") for d in last7) else None),
        "vigorous": comp("vigorousMinutesAvg") or (sum(d.get("vigorous_min") or 0 for d in last7) if any(d.get("vigorous_min") is not None for d in last7) else None),
        "steps": mean([d["steps"] for d in last7[:-1] if d.get("steps")]) if any(d.get("steps") for d in last7[:-1]) else None,
    }
    beds = [b for b in (_bed_minutes(d) for d in last14) if b is not None]
    vals["sleep_consistency"] = pstdev(beds) if len(beds) >= 4 else None

    specs = list(FACTORS)
    extras = {"rhr": extra("rhr"), "vigorous": extra("vigorousMinutesAvg")}
    if comp("vigorousDaysAvg") is not None:  # Garmin-only factor
        specs.insert(4, {
            "key": "vigorous_days", "label": "Vigorous Days (weekly avg)", "unit": "", "nd": 1,
            "optimal": lambda v, c: v >= 3, "sufficient": lambda v, c: v >= 2, "range": lambda c: "≥ 3 / week",
            "years": lambda v, c: _clamp((3 - v) * 0.5, -0.5, 1.5), "display": lambda v: f"{v:.1f} days"})
        vals["vigorous_days"] = comp("vigorousDaysAvg")
        extras["vigorous_days"] = extra("vigorousDaysAvg")
    body_fat = comp("bodyFat") or raw.get("body_fat")
    extras["body"] = extra("bodyFat") if comp("bodyFat") is not None else extra("bmi")
    if body_fat:
        specs = [(_bodyfat_factor(sex) if f["key"] == "body" else f) for f in specs]
        vals["body"] = body_fat
    else:
        vals["body"] = comp("bmi") or raw.get("bmi")

    ctx = {"age": age or 30, "sex": sex}
    factors, total_years = [], 0.0
    for f in specs:
        v = vals.get(f["key"])
        row = {"key": f["key"], "label": f["label"], "unit": f["unit"], "value": None, "display": "—",
               "optimal_range": f["range"](ctx), "status": "missing", "years": None,
               "garmin": extras.get(f["key"])}  # {'target','potential_gain','stale'} when Garmin supplied it
        if v is not None:
            v = float(v)
            yrs = round(f["years"](v, ctx), 1)
            total_years += yrs
            row.update(value=round(v, f["nd"]), years=yrs,
                       display=f["display"](v) if "display" in f else (f"{v:.{f['nd']}f}" + (f" {f['unit']}" if f["unit"] and f["unit"] != "min" else "") + (" min" if f["unit"] == "min" else "")),
                       status="optimal" if f["optimal"](v, ctx) else "sufficient" if f["sufficient"](v, ctx) else "out")
        factors.append(row)

    goop_est = round(age + total_years, 1) if age else None  # shown only as a footnote, never as the headline
    headline = raw.get("garmin_fitness_age")
    source = ("demo" if mode == "demo" else "garmin") if headline is not None else None
    headline = round(headline, 1) if headline is not None else None

    history = [h for h in (raw.get("history") or []) if h[1] is not None]
    if headline is not None and (not history or history[-1][0] != target.isoformat()):
        history.append([target.isoformat(), headline])
    ps = pace_series(history)
    pace = ps[-1][1] if ps else None
    pace_prev = ps[-2][1] if len(ps) >= 2 else None
    pts = [h for h in history if h[1] is not None]
    span_w = ((date.fromisoformat(pts[-1][0]) - date.fromisoformat(pts[0][0])).days // 7) if pts else 0
    counts = {s: sum(1 for f in factors if f["status"] == s) for s in ("optimal", "sufficient", "out")}
    return {
        "mode": mode, "date": target.isoformat(), "source": source,
        "chronological_age": round(age, 1) if age else None, "sex": sex,
        "goop_age": headline, "fitness_age": headline, "goop_estimate": goop_est,
        "achievable_age": round(raw["achievable"], 1) if raw.get("achievable") is not None else None,
        "previous_age": round(raw["previous"], 1) if raw.get("previous") is not None else None,
        "last_updated": raw.get("last_updated"),
        "years_younger": round(age - headline, 1) if (age and headline is not None) else None,
        "pace": pace, "pace_change": round(pace - pace_prev, 2) if (pace is not None and pace_prev is not None) else None,
        "pace_status": "ok" if pace is not None else "building",
        "pace_history": {"points": len(pts), "span_weeks": span_w, "changes": len({round(v, 2) for _, v in pts}) > 1,
                         "need_points": MIN_POINTS, "need_weeks": MIN_SPAN_DAYS // 7},
        "pace_series": ps[-12:],
        "history": history, "factors": factors, "counts": counts,
    }
