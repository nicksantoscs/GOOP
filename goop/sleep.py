"""Sleep: parser for Garmin get_sleep_data (field names verified against Nicholas's live account,
see goop_test/live_sleep_schema.json), Goop's sleep coach maths, and schema-shaped demo data.

Garmin units: sleepNeed.baseline/actual are MINUTES; *Seconds fields are seconds; *TimestampLocal are epoch ms
already shifted to local time; sleepLevels/hrv/etc. use GMT. sleepLevels.activityLevel: 0 deep, 1 light, 2 REM, 3 awake.
"""
from __future__ import annotations

import random
import statistics
from datetime import date, datetime, timedelta, timezone

STAGES = {0: "deep", 1: "light", 2: "rem", 3: "awake"}
ADJ = {"sleepHistoryAdjustment": "sleep history", "trainingFeedback": "training", "hrvAdjustment": "HRV", "napAdjustment": "nap"}
_KEEP_UPPER = {"HRV", "REM", "SPO2", "HR"}
_PHRASES = {  # common Garmin sleep enums -> plain English (anything else falls back to humanize())
    "NEGATIVE_SHORT_AND_POOR_STRUCTURE": "Short night with a poor stage mix",
    "NEGATIVE_LONG_BUT_POOR_QUALITY": "Long, but poor quality sleep",
    "NEGATIVE_NOT_ENOUGH_REM": "Not enough REM sleep", "NEGATIVE_NOT_ENOUGH_DEEP": "Not enough deep sleep",
    "NEGATIVE_SHORT": "Too little sleep", "NEGATIVE_FRAGMENTED": "Restless, fragmented sleep",
    "POSITIVE_LONG_AND_DEEP": "Long and deep sleep", "POSITIVE_OPTIMAL_STRUCTURE": "Well-balanced sleep stages",
    "POSITIVE_DEEP": "Plenty of deep sleep", "POSITIVE_REFRESHING": "Refreshing sleep", "POSITIVE_CONTINUOUS": "Continuous, undisturbed sleep",
    "STRESS_NEG_POOR_SLEEP_STRESSFUL_EVENING": "A stressful evening may have hurt your sleep",
    "LONG_DURATION_HIGH_NEED": "Long nap, your sleep need was high",
    "HIGHLY_INCREASED": "Sleep need is well above baseline", "INCREASED": "Sleep need is above baseline",
    "NO_CHANGE_BALANCED": "Sleep need matches baseline", "DECREASED": "Sleep need is below baseline",
}


def humanize(code) -> str | None:
    if not code or not isinstance(code, str) or code.upper() in ("NONE", "NO_DATA", "UNKNOWN"):
        return None
    if code in _PHRASES:
        return _PHRASES[code]
    w = [x for x in code.split("_") if x and not x.isdigit() and x not in ("POSITIVE", "NEGATIVE", "NEG", "POS")]
    if not w:
        return None
    out = [x if x in _KEEP_UPPER else x.lower() for x in w]
    out[0] = out[0] if out[0] in _KEEP_UPPER else out[0].capitalize()
    return " ".join(out)


def _local_iso(ms):
    if not isinstance(ms, (int, float)):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes")


def _gmt(s):
    if not s:
        return None
    try:
        if isinstance(s, (int, float)):
            return datetime.fromtimestamp(s / 1000, tz=timezone.utc).replace(tzinfo=None)
        return datetime.fromisoformat(str(s).replace("Z", "")[:19])
    except ValueError:
        return None


def _need(n) -> dict | None:
    if not isinstance(n, dict) or n.get("actual") is None and n.get("baseline") is None:
        return None
    adj = []
    for k, label in ADJ.items():
        v = n.get(k)
        if v in ("INCREASING", "DECREASING"):
            adj.append({"key": k, "label": label, "dir": "up" if v == "INCREASING" else "down"})
    base, act = n.get("baseline"), n.get("actual")
    return {"date": n.get("calendarDate"), "baseline_min": base, "actual_min": act,
            "delta_min": (act - base) if (act is not None and base is not None) else None,
            "feedback": n.get("feedback"), "feedback_text": humanize(n.get("feedback")), "adjustments": adj,
            "raw": {k: n.get(k) for k in ADJ}}


def need_chips(n: dict | None) -> list[str]:
    """'+60 min · sleep history', 'nap reduced need', ... (Garmin gives directions, not per-factor minutes)."""
    if not n:
        return []
    chips, adj, d = [], n["adjustments"], n.get("delta_min")
    if len(adj) == 1 and d:
        a = adj[0]
        return [f"{'+' if d > 0 else '−'}{abs(d)} min · {a['label']}"]
    if d:
        chips.append(f"{'+' if d > 0 else '−'}{abs(d)} min vs baseline")
    for a in adj:
        chips.append(f"{a['label']} {'raised' if a['dir'] == 'up' else 'reduced'} need")
    return chips


def parse_sleep(raw: dict | None) -> dict | None:
    """Normalise one night of get_sleep_data. Every field is optional."""
    if not isinstance(raw, dict):
        return None
    dto = raw.get("dailySleepDTO") or {}
    if not dto.get("sleepTimeSeconds"):
        return None
    sc = dto.get("sleepScores") or {}
    off_ms = 0
    if isinstance(dto.get("sleepStartTimestampLocal"), (int, float)) and isinstance(dto.get("sleepStartTimestampGMT"), (int, float)):
        off_ms = dto["sleepStartTimestampLocal"] - dto["sleepStartTimestampGMT"]
    off = timedelta(milliseconds=off_ms)

    def q(key):
        x = sc.get(key) or {}
        return x if isinstance(x, dict) else {}

    total = dto.get("sleepTimeSeconds")
    stages = {k: dto.get(f"{k}SleepSeconds") for k in ("deep", "light", "rem", "awake")}

    def row(key, label, value, display, s, unit_fmt):
        lo, hi = s.get("optimalStart"), s.get("optimalEnd")
        rng = None
        if lo is not None and hi is not None:
            rng = unit_fmt(lo) if lo == hi else f"{unit_fmt(lo)}–{unit_fmt(hi)}"
        return {"key": key, "label": label, "value": value, "display": display, "qualifier": s.get("qualifierKey"),
                "qualifier_text": humanize(s.get("qualifierKey")), "optimal": rng}

    hm = lambda sec: f"{round(sec / 60) // 60}h {round(sec / 60) % 60:02d}m" if sec is not None else "—"
    pct = lambda v: f"{v:.0f}%"
    rows = [row("duration", "Duration", total, hm(total), q("totalDuration"), hm)]
    for k, label in (("deep", "Deep"), ("light", "Light"), ("rem", "REM")):
        s = q(f"{k}Percentage")
        v = s.get("value")
        if v is None and stages.get(k) is not None and total:
            v = round(100 * stages[k] / total)
        rows.append(row(k, label, v, (f"{v}% · {hm(stages.get(k))}" if v is not None else "—"), s, pct))
    ac = dto.get("awakeCount")
    rows.append(row("awake", "Awake / wake-ups", ac, (f"{ac} wake-ups · {hm(stages.get('awake'))}" if ac is not None else hm(stages.get("awake"))),
                    q("awakeCount"), lambda v: f"{v:.0f}"))
    rm = raw.get("restlessMomentsCount")
    rows.append(row("restless", "Restlessness", rm, f"{rm} restless moments" if rm is not None else "—", q("restlessness"), lambda v: f"{v:.0f}"))
    ss = dto.get("avgSleepStress")
    rows.append(row("stress", "Sleep stress", ss, f"{ss:.0f} avg" if ss is not None else "—", q("stress"), lambda v: f"{v:.0f}"))

    levels = []
    for x in raw.get("sleepLevels") or []:
        if not isinstance(x, dict):
            continue
        a, b = _gmt(x.get("startGMT")), _gmt(x.get("endGMT"))
        lv = x.get("activityLevel")
        if a and b and lv is not None and int(round(lv)) in STAGES:
            levels.append([(a + off).isoformat(timespec="minutes"), (b + off).isoformat(timespec="minutes"), STAGES[int(round(lv))]])

    naps = []
    for n in dto.get("dailyNapDTOS") or []:
        if not isinstance(n, dict):
            continue
        a, b = _gmt(n.get("napStartTimestampGMT")), _gmt(n.get("napEndTimestampGMT"))
        # napStartTimeOffset = minutes to add to GMT (e.g. -240 in EDT); else reuse the night's offset
        o = timedelta(minutes=n["napStartTimeOffset"]) if isinstance(n.get("napStartTimeOffset"), (int, float)) else off
        naps.append({"sec": n.get("napTimeSec"), "start": (a + o).isoformat(timespec="minutes") if a else None,
                     "end": (b + o).isoformat(timespec="minutes") if b else None,
                     "feedback": n.get("napFeedback"), "feedback_text": humanize(n.get("napFeedback"))})

    ov = sc.get("overall") or {}
    return {
        "score": ov.get("value"), "qualifier": ov.get("qualifierKey"), "qualifier_text": humanize(ov.get("qualifierKey")),
        "feedback": dto.get("sleepScoreFeedback"), "feedback_text": humanize(dto.get("sleepScoreFeedback")),
        "insight_text": humanize(dto.get("sleepScoreInsight")),
        "personal_text": humanize(dto.get("sleepScorePersonalizedInsight")),
        "start_local": _local_iso(dto.get("sleepStartTimestampLocal")), "end_local": _local_iso(dto.get("sleepEndTimestampLocal")),
        "total_s": total, "nap_s": dto.get("napTimeSeconds") or sum(n["sec"] or 0 for n in naps) or 0, "stages": stages,
        "rows": rows, "levels": levels, "naps": naps,
        "measures": {
            "hrv": raw.get("avgOvernightHrv"), "hrv_status": raw.get("hrvStatus") if raw.get("hrvStatus") not in (None, "NONE") else None,
            "resp": dto.get("averageRespirationValue"), "resp_low": dto.get("lowestRespirationValue"), "resp_high": dto.get("highestRespirationValue"),
            "spo2_avg": dto.get("averageSpO2Value"), "spo2_low": dto.get("lowestSpO2Value"),
            "avg_hr": dto.get("avgHeartRate"), "rhr": raw.get("restingHeartRate"), "bb_change": raw.get("bodyBatteryChange"),
            "skin_temp": raw.get("avgSkinTempDeviationC"),
        },
        "need": _need(dto.get("sleepNeed")), "next_need": _need(dto.get("nextSleepNeed")),
    }


# ------------------------------------------------------------------ coach maths (Goop-computed)
def _clock_min(iso, pivot=12 * 60):
    """Minutes since midnight, with times before `pivot` pushed to the next day (bedtimes around midnight)."""
    if not iso or len(iso) < 16:
        return None
    m = int(iso[11:13]) * 60 + int(iso[14:16])
    return m + 1440 if m < pivot else m


def _hhmm(m):
    m = int(round(m)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def coach(nights: list[dict | None], tonight_need: dict | None) -> dict:
    """nights: parsed sleep for the last 7 days (oldest -> newest; None = no data)."""
    have = [n for n in nights if n]
    wakes = [_clock_min(n["end_local"], pivot=0) for n in have if n.get("end_local")]
    beds = [_clock_min(n["start_local"]) for n in have if n.get("start_local")]
    need_min = (tonight_need or {}).get("actual_min")
    usual_wake = statistics.median(wakes) if wakes else None
    bedtime = (usual_wake - need_min) if (usual_wake is not None and need_min) else None
    debt, counted = 0, 0
    for n in have:
        nd = (n.get("need") or {}).get("actual_min") or (n.get("need") or {}).get("baseline_min")
        if nd:
            debt += max(0, nd * 60 - ((n.get("total_s") or 0) + (n.get("nap_s") or 0)))
            counted += 1
    cons = None
    if len(beds) >= 3 and len(wakes) >= 3:
        sd = (statistics.pstdev(beds) + statistics.pstdev(wakes)) / 2
        cons = {"score": max(0, min(100, round(100 - sd * 100 / 90))), "sd_bed_min": round(statistics.pstdev(beds)),
                "sd_wake_min": round(statistics.pstdev(wakes)), "nights": len(beds)}
    return {
        "tonight_need_min": need_min, "tonight_need": tonight_need, "chips": need_chips(tonight_need),
        "usual_wake": _hhmm(usual_wake) if usual_wake is not None else None,
        "bedtime": _hhmm(bedtime) if bedtime is not None else None,
        "debt_s": debt if counted else None, "debt_nights": counted, "consistency": cons,
    }


# ------------------------------------------------------------------ demo (same shape as the live payload)
def demo_raw(d: str, total: int, deep: int, rem: int, awake: int, start_local: datetime, score: int) -> dict:
    r = random.Random("sleep" + d)
    light = total - deep - rem
    off = timedelta(hours=-4)
    start_gmt = start_local - off
    end_local = start_local + timedelta(seconds=total + awake)
    ep = lambda dt: int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
    # stage timeline: ~90-min cycles, deep early, REM late, a few wake-ups
    levels, t, left = [], start_gmt, {"deep": deep, "light": light, "rem": rem, "awake": awake}
    cyc = 0
    while sum(left.values()) > 60 and cyc < 40:
        plan = [("light", 900), ("deep", 1500 - cyc * 250), ("light", 1200), ("rem", 600 + cyc * 300), ("awake", 240)]
        for st, dur in plan:
            dur = min(max(0, int(dur * r.uniform(.7, 1.2))), left[st])
            if dur < 60:
                continue
            levels.append({"startGMT": t.isoformat() + ".0", "endGMT": (t + timedelta(seconds=dur)).isoformat() + ".0",
                           "activityLevel": float({"deep": 0, "light": 1, "rem": 2, "awake": 3}[st])})
            t += timedelta(seconds=dur)
            left[st] -= dur
        cyc += 1
    qual = lambda v, lo, hi: "EXCELLENT" if lo <= v <= hi else "GOOD" if lo - 5 <= v <= hi + 5 else "FAIR"
    dp, rp, lp = round(100 * deep / total), round(100 * rem / total), round(100 * light / total)
    base = 480
    hist = "INCREASING" if total < 7 * 3600 else "NO_CHANGE"
    nap = 1800 if r.random() < 0.3 else 0
    need = {"baseline": base, "actual": base + (40 if hist == "INCREASING" else 0) + r.choice([0, 0, 10]),
            "feedback": "INCREASED" if hist == "INCREASING" else "NO_CHANGE_BALANCED", "trainingFeedback": r.choice(["NO_CHANGE", "INCREASING"]),
            "sleepHistoryAdjustment": hist, "hrvAdjustment": "NO_CHANGE", "napAdjustment": "NO_CHANGE", "calendarDate": d}
    nxt_d = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
    nxt = {**need, "calendarDate": nxt_d, "actual": need["actual"] - (20 if nap else 0) + (10 if need["trainingFeedback"] == "INCREASING" else 0),
           "napAdjustment": "DECREASING" if nap else "NO_CHANGE"}
    ac = r.randint(1, 4)
    naps = []
    if nap:
        ns = datetime.fromisoformat(d) + timedelta(hours=14, minutes=r.randint(0, 50)) - off
        naps = [{"napTimeSec": nap, "napStartTimestampGMT": ns.isoformat(), "napEndTimestampGMT": (ns + timedelta(seconds=nap)).isoformat(),
                 "napFeedback": "IDEAL_TIMING_IDEAL_DURATION_LOW_NEED", "napStartTimeOffset": -240, "napEndTimeOffset": -240}]
    return {
        "dailySleepDTO": {
            "calendarDate": d, "sleepTimeSeconds": total, "napTimeSeconds": nap,
            "sleepStartTimestampGMT": ep(start_gmt), "sleepEndTimestampGMT": ep(start_gmt + timedelta(seconds=total + awake)),
            "sleepStartTimestampLocal": ep(start_local), "sleepEndTimestampLocal": ep(end_local),
            "deepSleepSeconds": deep, "lightSleepSeconds": light, "remSleepSeconds": rem, "awakeSleepSeconds": awake,
            "averageSpO2Value": float(r.randint(95, 98)), "lowestSpO2Value": r.randint(88, 93),
            "averageRespirationValue": round(r.uniform(13.2, 14.8), 1), "lowestRespirationValue": 10.0, "highestRespirationValue": 19.0,
            "awakeCount": ac, "avgSleepStress": float(r.randint(10, 24)), "avgHeartRate": float(r.randint(52, 60)),
            "sleepScoreFeedback": "POSITIVE_OPTIMAL_STRUCTURE" if score >= 80 else "NEGATIVE_NOT_ENOUGH_REM" if score >= 65 else "NEGATIVE_SHORT_AND_POOR_STRUCTURE",
            "sleepScoreInsight": "NONE", "sleepScorePersonalizedInsight": "STRESS_NEG_POOR_SLEEP_STRESSFUL_EVENING" if score < 65 else "NONE",
            "sleepScores": {
                "overall": {"value": score, "qualifierKey": "EXCELLENT" if score >= 90 else "GOOD" if score >= 80 else "FAIR" if score >= 60 else "POOR"},
                "totalDuration": {"qualifierKey": "GOOD" if total >= 7 * 3600 else "FAIR" if total >= 6 * 3600 else "POOR", "optimalStart": 28800.0, "optimalEnd": 28800.0},
                "stress": {"qualifierKey": "GOOD", "optimalStart": 0.0, "optimalEnd": 15.0},
                "awakeCount": {"qualifierKey": "GOOD" if ac <= 1 else "FAIR" if ac <= 2 else "POOR", "optimalStart": 0.0, "optimalEnd": 1.0},
                "restlessness": {"qualifierKey": "FAIR", "optimalStart": 0.0, "optimalEnd": 5.0},
                "remPercentage": {"value": rp, "qualifierKey": qual(rp, 21, 31), "optimalStart": 21.0, "optimalEnd": 31.0},
                "lightPercentage": {"value": lp, "qualifierKey": qual(lp, 30, 64), "optimalStart": 30.0, "optimalEnd": 64.0},
                "deepPercentage": {"value": dp, "qualifierKey": qual(dp, 16, 33), "optimalStart": 16.0, "optimalEnd": 33.0},
            },
            "sleepNeed": need, "nextSleepNeed": nxt, "dailyNapDTOS": naps,
        },
        "sleepLevels": levels, "restlessMomentsCount": r.randint(18, 45), "avgOvernightHrv": float(r.randint(48, 72)),
        "hrvStatus": "BALANCED", "bodyBatteryChange": r.randint(40, 70), "restingHeartRate": r.randint(50, 56),
        "avgSkinTempDeviationC": round(r.uniform(-0.6, 0.6), 1),
    }
