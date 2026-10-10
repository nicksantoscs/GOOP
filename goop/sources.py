"""Data sources: live Garmin Connect (via the open-source `garminconnect` lib) or demo data.

Both return the same normalised per-day dict so the UI never cares which one is active.
"""
from __future__ import annotations

import logging
import math
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("goop")

from goop.tokens import TOKEN_DIR
from goop.sleep import demo_raw as demo_sleep_raw, parse_sleep  # noqa: E402,F401  (kept for older imports)


def _g(d, *path, default=None):
    """Safe nested getter for Garmin's loosely-shaped JSON."""
    cur = d
    for p in path:
        if cur is None:
            return default
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and isinstance(p, int) and -len(cur) <= p < len(cur):
            cur = cur[p]
        else:
            return default
    return default if cur is None else cur


def _ts(v):
    """Garmin '...TimestampLocal' values are epoch ms already shifted to local time; render as naive ISO."""
    if isinstance(v, (int, float)):
        from datetime import timezone
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes")
    return v


def _empty_day(d: str) -> dict:
    return {
        "date": d, "rhr": None, "hrv": None, "hrv_weekly": None, "hrv_status": None, "hrv_base_low": None, "hrv_base_high": None, "hrv_night": None,
        "sleep": None, "body_battery": None, "steps": None, "step_goal": None,
        "active_kcal": None, "moderate_min": 0, "vigorous_min": 0, "stress_avg": None,
        "activities": [], "activity_load": 0.0, "readiness": None, "training_status": None,
        "current_hr": None, "hr_series": [], "max_hr": None, "min_hr": None,
        "total_kcal": None, "stress": None, "respiration": None, "vo2max": None,
        "series": {"hr": [], "spo2": [], "hrv": [], "resp": [], "stress": [], "bb": []},
        "spo2": None, "skin_temp_dev": None, "stress_timeline": [], "stress_durations": None, "stress_bands": None,
        "zone_s": None, "activity_s": 0, "strength_s": 0, "sleep_need_s": None, "light": False, "sleep_detail": None,
    }


def _zones(acts: list[dict]) -> list[int] | None:
    """Sum hrTimeInZone_1..5 (seconds) across activities; None if Garmin gave no zone data."""
    tot, seen = [0] * 5, False
    for a in acts:
        for i in range(5):
            v = a.get(f"hr_z{i + 1}")
            if v is not None:
                seen = True
                tot[i] += int(v)
    return tot if seen else None


def _hhmm(t) -> str | None:
    """epoch-ms (local clock), epoch-ms GMT, or ISO string -> 'HH:MM' local."""
    if t is None:
        return None
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(t / 1000).strftime("%H:%M")
    t = str(t)
    return t[11:16] if len(t) >= 16 and t[10] in "T " else None


def _series(points, cap: int = 150, valid=lambda v: v is not None and v > 0) -> list:
    """Normalise Garmin time series ([[ts, v], [ts, status, v, ver], or dicts]) to [['HH:MM', v], ...]."""
    out = []
    for p in points or []:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            ts, v = p[0], (p[2] if len(p) >= 4 else p[1])
        elif isinstance(p, dict):
            ts = next((p[k] for k in ("readingTimeLocal", "epochTimestamp", "startGMT", "timestamp") if p.get(k) is not None), None)
            v = next((p[k] for k in ("hrvValue", "spO2Reading", "value", "respirationValue") if p.get(k) is not None), None)
        else:
            continue
        if isinstance(v, (int, float)) and valid(v):
            hm = _hhmm(ts)
            if hm:
                out.append([hm, v])
    step = max(1, len(out) // cap)
    return out[::step]


def stress_bands3(points: list) -> dict | None:
    """Seconds spent in WHOOP-style 0-3 stress bands from [['HH:MM', garmin 0-100], ...] (full-res series).
    LOW 0-<1, MEDIUM 1-<2, HIGH 2-3 on the 0-3 scale (= garmin value * 3 / 100). Each sample lasts until
    the next one (gaps capped at 15 min so unmeasured stretches aren't counted)."""
    if not points:
        return None
    mins = []
    off, prev = 0, None
    for t, _ in points:
        m = int(t[:2]) * 60 + int(t[3:5]) + off
        if prev is not None and m < prev - 600:
            off += 1440; m += 1440
        mins.append(m); prev = m
    gaps = [b - a for a, b in zip(mins, mins[1:]) if b > a]
    typical = sorted(gaps)[len(gaps) // 2] if gaps else 3
    out = {"low": 0, "medium": 0, "high": 0}
    for i, (_, v) in enumerate(points):
        g = (mins[i + 1] - mins[i]) if i + 1 < len(points) else typical
        g = max(0, min(g, 15))
        x = v * 3 / 100
        out["low" if x < 1 else "medium" if x < 2 else "high"] += g * 60
    return out


def _bb_summary(series: list, base: dict | None, charged=None, drained=None) -> dict:
    """Body Battery card data: current, latest reading time, charged/drained today."""
    out = dict(base or {})
    if series:
        out["current"] = series[-1][1] if out.get("current") is None else out["current"]
        out["latest_at"] = series[-1][0]
        if charged is None or drained is None:
            ups = sum(max(0, b[1] - a[1]) for a, b in zip(series, series[1:]))
            downs = sum(max(0, a[1] - b[1]) for a, b in zip(series, series[1:]))
            charged = ups if charged is None else charged
            drained = downs if drained is None else drained
    out["charged"], out["drained"] = charged, drained
    return out


def _finish(out: dict) -> dict:
    acts = out["activities"]
    out["activity_load"] = float(sum((x.get("load") or 0) for x in acts))
    out["activity_s"] = int(sum((x.get("duration_s") or 0) for x in acts))
    out["strength_s"] = int(sum((x.get("duration_s") or 0) for x in acts if "strength" in (x.get("type") or "")))
    out["zone_s"] = _zones(acts)
    return out


# --------------------------------------------------------------------------- Garmin
def open_from_tokens(tokens_json: str):
    """Authenticated Garmin client from serialized OAuth tokens (no password involved)."""
    from garminconnect import Garmin

    api = Garmin()
    api.login(tokenstore=tokens_json)
    return api


READINESS_URL = "/metrics-service/metrics/trainingreadiness"


def get_morning_training_readiness(api, day: str) -> dict | None:
    """Garmin's Morning Report readiness for `day`: the AFTER_WAKEUP_RESET snapshot from
    /metrics-service/metrics/trainingreadiness/{day}; falls back to the first entry when no
    snapshot is tagged (older firmware). Empty list / nothing -> None. Errors propagate."""
    raw = api.connectapi(f"{READINESS_URL}/{day}")
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, list):
        return None
    rows = [x for x in raw if isinstance(x, dict)]
    if not rows:
        return None
    return next((x for x in rows if x.get("inputContext") == "AFTER_WAKEUP_RESET"), rows[0])


def parse_readiness(r: dict | None) -> dict | None:
    if not r:
        return None
    pct = lambda k: r.get(k) if isinstance(r.get(k), (int, float)) else None
    return {
        "score": r.get("score"), "level": r.get("level"),
        "feedback_short": r.get("feedbackShort"), "feedback_long": r.get("feedbackLong"),
        "recovery_min": r.get("recoveryTime"), "recovery_phrase": r.get("recoveryTimeChangePhrase"),
        "at": r.get("timestampLocal") or r.get("timestamp"), "context": r.get("inputContext"),
        "factors": [
            {"key": "sleep", "label": "Sleep", "pct": pct("sleepScoreFactorPercent"), "feedback": r.get("sleepScoreFactorFeedback")},
            {"key": "hrv", "label": "HRV status", "pct": pct("hrvFactorPercent"), "feedback": r.get("hrvFactorFeedback")},
            {"key": "load", "label": "Acute load (ACWR)", "pct": pct("acwrFactorPercent"), "feedback": r.get("acwrFactorFeedback")},
            {"key": "stress", "label": "Stress history", "pct": pct("stressHistoryFactorPercent"), "feedback": r.get("stressHistoryFactorFeedback")},
        ],
    }


HISTORY_WEEKS = 26  # weekly Fitness Age points (plus today) behind Pace of Aging


class GarminSource:
    mode = "garmin"

    def __init__(self, api=None):
        """Live source. `api` = an already-authenticated Garmin instance (in-app sign-in);
        otherwise sign in from stored OAuth tokens (keychain / fallback file), or as a last resort
        from GARMIN_EMAIL / GARMIN_PASSWORD env vars (no MFA support there)."""
        from garminconnect import Garmin  # imported lazily so demo mode needs nothing

        from goop import tokens

        self._lock = threading.Lock()
        self._cache: dict = {}
        self._failed: dict = {}  # (call name, args) -> time of last failure; cleared on success
        self.last_sync: float | None = None
        stored = tokens.load()
        if api is None:
            if stored and stored.get("garmin"):
                api = open_from_tokens(stored["garmin"])  # loads tokens + profile; refreshes if needed
            else:
                email, password = os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD")
                if not (email and password):
                    raise RuntimeError("Not connected. Connect Garmin in Settings.")
                first = Garmin(email=email, password=password, return_on_mfa=True)
                mfa, _ = first.login()
                if mfa:
                    raise RuntimeError("Garmin requires MFA. Connect Garmin in Settings (or run setup_garmin.py).")
                api = open_from_tokens(first.client.dumps())  # re-open so profile/settings load normally
                stored = {"email": email}
        self.api = api
        self.name = getattr(api, "full_name", None) or getattr(api, "display_name", None) or (stored or {}).get("name")
        self.email = (stored or {}).get("email") or getattr(api, "username", None)
        self._saved_tokens = None
        self.persist_tokens()

    def persist_tokens(self) -> None:
        """Save the (possibly refreshed) OAuth tokens; never the password."""
        from goop import tokens
        try:
            t = self.api.client.dumps()
        except Exception:  # noqa: BLE001
            return
        if t != self._saved_tokens:
            tokens.save(t, email=self.email, name=self.name)
            self._saved_tokens = t

    def _call(self, fn, *a):
        k = (getattr(fn, "__name__", str(fn)), a)
        try:
            out = fn(*a)
            self._failed.pop(k, None)
            return out
        except Exception as e:  # noqa: BLE001  (missing metric on this device/day is normal)
            log.debug("%s%s failed: %s", k[0], a, type(e).__name__)
            self._failed[k] = time.time()
            return None

    def failed_calls(self, dates: list[str]) -> list[str]:
        """Names of Garmin calls that errored (not just returned nothing) for these dates."""
        return sorted({n for (n, a), _ in list(self._failed.items()) if a and a[0] in dates})

    def morning(self, d: str) -> dict:
        """Morning Report readiness (cached 10 min for today, 6 h for past days)."""
        k = ("morning", d)
        ttl = 600 if d == date.today().isoformat() else 6 * 3600
        with self._lock:
            hit = self._cache.get(k)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        try:
            out = {"readiness": parse_readiness(get_morning_training_readiness(self.api, d)), "error": None}
        except Exception as e:  # noqa: BLE001
            log.info("morning readiness failed: %s", type(e).__name__)
            out = {"readiness": None, "error": type(e).__name__}
        with self._lock:
            self._cache[k] = (time.time(), out)
        return out

    def day(self, d: str, light: bool = False) -> dict:
        """light=True fetches only daily stats + sleep (2 calls) for long-range averages."""
        ttl = 300 if d == date.today().isoformat() else 6 * 3600
        with self._lock:
            for k in ((d, False),) if light is False else ((d, False), (d, True)):
                hit = self._cache.get(k)
                if hit and time.time() - hit[0] < ttl:
                    return hit[1]
        out = self._fetch_day(d, light)
        with self._lock:
            self._cache[(d, light)] = (time.time(), out)
            self.last_sync = time.time()
        self.persist_tokens()
        return out

    def _fetch_day(self, d: str, light: bool = False) -> dict:
        a = self.api
        out = _empty_day(d)
        out["light"] = light
        stats = self._call(a.get_stats, d) or {}
        sleep = self._call(a.get_sleep_data, d) or {}
        hrv = self._call(a.get_hrv_data, d) or {}  # also on light days: RHR+HRV 30-day chart
        if light:
            hr = stress = mm = spo2 = {}
            tr, acts = [], []
        else:
            hr = self._call(a.get_heart_rates, d) or {}
            tr = self._call(a.get_training_readiness, d) or []
            acts = self._call(a.get_activities_by_date, d, d) or []
            stress = self._call(a.get_stress_data, d) or {}
            mm = self._call(a.get_max_metrics, d) or {}
            spo2 = self._call(a.get_spo2_data, d) or {}

        out["rhr"] = stats.get("restingHeartRate") or hr.get("restingHeartRate")
        out["steps"] = stats.get("totalSteps")
        out["step_goal"] = stats.get("dailyStepGoal")
        out["active_kcal"] = stats.get("activeKilocalories")
        out["moderate_min"] = stats.get("moderateIntensityMinutes") or 0
        out["vigorous_min"] = stats.get("vigorousIntensityMinutes") or 0
        out["stress_avg"] = stats.get("averageStressLevel")
        out["total_kcal"] = stats.get("totalKilocalories")
        sv = [(ts, v) for ts, v in (stress.get("stressValuesArray") or []) if v is not None and v >= 0]
        out["series"]["stress"] = _series(sv, valid=lambda v: v >= 0)
        out["stress_bands"] = stress_bands3(_series(sv, cap=10 ** 6, valid=lambda v: v >= 0))
        out["stress_timeline"] = out["series"]["stress"]
        bbv = stress.get("bodyBatteryValuesArray")
        bb_day = {}
        if not light:  # get_body_battery gives charged/drained totals (and the series if stress lacked it)
            bbd = self._call(a.get_body_battery, d) or []
            bb_day = (bbd[0] or {}) if isinstance(bbd, list) and bbd else {}
            bbv = bbv or bb_day.get("bodyBatteryValuesArray")
        out["series"]["bb"] = _series(bbv, valid=lambda v: v >= 0)
        out["series"]["hr"] = _series(hr.get("heartRateValues"))
        out["series"]["hrv"] = _series(hrv.get("hrvReadings"))
        hv = [p.get("hrvValue") for p in (hrv.get("hrvReadings") or []) if p.get("hrvValue")]
        out["hrv_night"] = {  # 5-min overnight HRV readings: Garmin only measures during sleep
            "start": _hhmm(hrv.get("startTimestampLocal")), "end": _hhmm(hrv.get("endTimestampLocal")),
            "sleep_start": _hhmm(hrv.get("sleepStartTimestampLocal")), "sleep_end": _hhmm(hrv.get("sleepEndTimestampLocal")),
            "n": len(hv), "min": min(hv) if hv else None, "max": max(hv) if hv else None,
            "avg": (hrv.get("hrvSummary") or {}).get("lastNightAvg"),
            "high_5min": (hrv.get("hrvSummary") or {}).get("lastNight5MinHigh")} if hrv else None
        sp_pts = spo2.get("continuousReadingDTOList") or spo2.get("spO2SingleValues") or spo2.get("spO2HourlyAverages")
        out["series"]["spo2"] = _series(sp_pts)
        if not light:
            resp = self._call(a.get_respiration_data, d) or {}
            out["series"]["resp"] = _series(resp.get("respirationValuesArray"))
        durs = [stats.get(f"{k}StressDuration") for k in ("rest", "low", "medium", "high")]
        if any(x is not None for x in durs):
            out["stress_durations"] = dict(zip(("rest", "low", "medium", "high"), [x or 0 for x in durs]))
        elif sv:  # Garmin samples stress every ~3 min; estimate from the series
            dt = 180
            out["stress_durations"] = {
                "rest": dt * sum(v <= 25 for _, v in sv), "low": dt * sum(25 < v <= 50 for _, v in sv),
                "medium": dt * sum(50 < v <= 75 for _, v in sv), "high": dt * sum(v > 75 for _, v in sv)}
        if sv or out["stress_avg"] is not None:
            out["stress"] = {"avg": out["stress_avg"], "latest": sv[-1][1] if sv else None,
                             "latest_at": datetime.fromtimestamp(sv[-1][0] / 1000).isoformat(timespec="minutes") if sv else None}
        out["max_hr"] = stats.get("maxHeartRate") or hr.get("maxHeartRate")
        out["min_hr"] = stats.get("minHeartRate") or hr.get("minHeartRate")
        out["body_battery"] = {
            "wake": stats.get("bodyBatteryAtWakeTime"),
            "current": stats.get("bodyBatteryMostRecentValue"),
            "high": stats.get("bodyBatteryHighestValue"),
            "low": stats.get("bodyBatteryLowestValue"),
        }
        out["body_battery"] = _bb_summary(
            out["series"]["bb"], out["body_battery"],
            charged=bb_day.get("charged", stats.get("bodyBatteryChargedValue")) if not light else None,
            drained=bb_day.get("drained", stats.get("bodyBatteryDrainedValue")) if not light else None)

        dto = sleep.get("dailySleepDTO") or {}
        if dto.get("sleepTimeSeconds"):
            out["sleep"] = {
                "score": _g(dto, "sleepScores", "overall", "value"),
                "total_s": dto.get("sleepTimeSeconds"),
                "deep_s": dto.get("deepSleepSeconds"),
                "light_s": dto.get("lightSleepSeconds"),
                "rem_s": dto.get("remSleepSeconds"),
                "awake_s": dto.get("awakeSleepSeconds"),
                "start_local": _ts(dto.get("sleepStartTimestampLocal")),
                "end_local": _ts(dto.get("sleepEndTimestampLocal")),
            }
            need = _g(dto, "sleepNeed", "actual") or _g(sleep, "sleepNeed", "actual")
            out["sleep_need_s"] = int(need) * 60 if need else None
            out["sleep"]["nap_s"] = dto.get("napTimeSeconds") or 0
            if not light:
                out["sleep_detail"] = parse_sleep(sleep)
        out["respiration"] = (dto.get("averageRespirationValue") or stats.get("avgWakingRespirationValue")
                              or stats.get("averageRespirationValue"))
        sp_latest = spo2.get("latestSpO2") or stats.get("latestSpo2")
        sp_avg = spo2.get("averageSpO2") or stats.get("averageSpo2") or dto.get("averageSpO2Value")
        if sp_latest or sp_avg:
            out["spo2"] = {"avg": sp_avg, "latest": sp_latest, "lowest": spo2.get("lowestSpO2") or stats.get("lowestSpo2"),
                           "latest_at": _ts(spo2.get("latestSpO2TimestampLocal") or stats.get("latestSpo2ReadingTimeLocal"))}
        # Skin temperature deviation (°C vs Garmin's own baseline) only exists on newer watches.
        out["skin_temp_dev"] = (sleep.get("avgSkinTempDeviationC") if sleep.get("avgSkinTempDeviationC") is not None
                                else dto.get("avgSkinTempDeviationC"))
        m0 = mm[0] if isinstance(mm, list) and mm else mm if isinstance(mm, dict) else {}
        out["vo2max"] = (_g(m0, "generic", "vo2MaxPreciseValue") or _g(m0, "generic", "vo2MaxValue")
                         or _g(m0, "cycling", "vo2MaxPreciseValue"))

        hs = hrv.get("hrvSummary") or {}
        out["hrv"] = hs.get("lastNightAvg") or sleep.get("avgOvernightHrv")
        out["hrv_weekly"] = hs.get("weeklyAvg")
        out["hrv_status"] = hs.get("status")
        bl = hs.get("baseline") or {}
        out["hrv_base_low"], out["hrv_base_high"] = bl.get("balancedLow"), bl.get("balancedUpper")

        if isinstance(tr, list) and tr:
            t0 = tr[0]
            out["readiness"] = {"score": t0.get("score"), "level": t0.get("level")}
        elif isinstance(tr, dict) and tr.get("score") is not None:
            out["readiness"] = {"score": tr.get("score"), "level": tr.get("level")}

        series = [(ts, v) for ts, v in (hr.get("heartRateValues") or []) if v]
        if series:
            ts, v = series[-1]
            out["current_hr"] = {"bpm": v, "at": datetime.fromtimestamp(ts / 1000).isoformat(timespec="minutes")}
            step = max(1, len(series) // 96)
            out["hr_series"] = [v for _, v in series[::step]]

        for x in acts:
            out["activities"].append({
                "name": x.get("activityName"),
                "type": _g(x, "activityType", "typeKey", default="activity"),
                "start_local": x.get("startTimeLocal"),
                "duration_s": x.get("duration"),
                "distance_m": x.get("distance"),
                "avg_hr": x.get("averageHR"),
                "max_hr": x.get("maxHR"),
                "kcal": x.get("calories"),
                "load": x.get("activityTrainingLoad"),
                "aerobic_te": x.get("aerobicTrainingEffect"),
                **{f"hr_z{i}": x.get(f"hrTimeInZone_{i}") for i in range(1, 6)},
            })
        _finish(out)

        if d == date.today().isoformat():
            ts_ = self._call(a.get_training_status, d) or {}
            latest = _g(ts_, "mostRecentTrainingStatus", "latestTrainingStatusData", default={}) or {}
            for dev in latest.values():
                if isinstance(dev, dict):
                    out["training_status"] = {
                        "status": dev.get("trainingStatusFeedbackPhrase") or dev.get("trainingStatus"),
                        "acute_load": _g(dev, "acuteTrainingLoadDTO", "dailyTrainingLoadAcute"),
                        "chronic_load": _g(dev, "acuteTrainingLoadDTO", "dailyTrainingLoadChronic"),
                    }
                    break
        return out

    def fitness(self, d: str) -> dict:
        """Raw inputs for the Goop Age screen (cached 6 h): Garmin Fitness Age (+12 weekly history points),
        profile (birth date, sex, height/weight) and recent body composition."""
        k = ("fitness", d)
        with self._lock:
            hit = self._cache.get(k)
            if hit and time.time() - hit[0] < 6 * 3600:
                return hit[1]
        a = self.api
        fa = self._call(a.get_fitnessage_data, d) or {}
        prof = self._call(a.get_user_profile) or {}
        ud = prof.get("userData") or prof
        start = (date.fromisoformat(d) - timedelta(days=30)).isoformat()
        body = self._call(a.get_body_composition, start, d) or {}
        tot = body.get("totalAverage") or {}
        age = None
        if os.getenv("GOOP_BIRTHDATE") or ud.get("birthDate"):
            try:
                bd = date.fromisoformat((os.getenv("GOOP_BIRTHDATE") or ud.get("birthDate"))[:10])
                age = (date.fromisoformat(d) - bd).days / 365.25
            except ValueError:
                pass
        if fa.get("chronologicalAge") is not None and not os.getenv("GOOP_BIRTHDATE"):
            age = float(fa["chronologicalAge"])  # Garmin's own chronological age wins in live mode
        age = age or fa.get("chronologicalAge")
        sex = (os.getenv("GOOP_SEX") or ud.get("gender") or "").lower() or None
        weight_kg = (tot.get("weight") or ud.get("weight") or 0) / 1000 or None
        height_m = (ud.get("height") or 0) / 100 or None
        bmi = tot.get("bmi") or (round(weight_kg / height_m ** 2, 1) if weight_kg and height_m else None)
        hist_dates = [(date.fromisoformat(d) - timedelta(weeks=w)).isoformat() for w in range(HISTORY_WEEKS, 0, -1)]
        with ThreadPoolExecutor(max_workers=3) as ex:
            past = list(ex.map(self._fitnessage_cached, hist_dates))
        history = [[x, p.get("fitnessAge")] for x, p in zip(hist_dates, past)]
        history.append([d, fa.get("fitnessAge")])
        out = {
            "age": age, "sex": sex if sex in ("male", "female") else None,
            "garmin_fitness_age": fa.get("fitnessAge"), "achievable": fa.get("achievableFitnessAge"),
            "components": fa.get("components") or {}, "bmi": bmi, "body_fat": tot.get("bodyFat"),
            "vo2max": None, "history": history,
            "previous": fa.get("previousFitnessAge"), "last_updated": fa.get("lastUpdated"),
        }
        with self._lock:
            self._cache[k] = (time.time(), out)
        return out

    def _fitnessage_cached(self, d: str) -> dict:
        """Garmin Fitness Age for one past week-date, cached 6 h per date (a failed call isn't cached)."""
        k = ("fa", d)
        with self._lock:
            hit = self._cache.get(k)
            if hit and time.time() - hit[0] < 6 * 3600:
                return hit[1]
        out = self._call(self.api.get_fitnessage_data, d)
        if out is not None:
            with self._lock:
                self._cache[k] = (time.time(), out)
        return out or {}

    def ecg(self, d: str) -> dict:
        """Recent ECG recordings. garminconnect 0.3.17 has NO ECG wrapper and no publicly documented
        Connect endpoint is known, so Goop only calls one if you set GOOP_ECG_PATH (e.g. copied from the
        Garmin Connect web app's network tab; '{date}' is substituted). 403/404 => not available."""
        path = os.getenv("GOOP_ECG_PATH")
        if not path:
            return {"available": False, "reason": "ECG not available from Garmin for this account.",
                    "detail": "The garminconnect library has no ECG API.", "readings": []}
        k = ("ecg", d)
        with self._lock:
            hit = self._cache.get(k)
            if hit and time.time() - hit[0] < 1800:
                return hit[1]
        try:
            raw = self.api.connectapi(path.replace("{date}", d))
            out = {"available": True, "readings": parse_ecg(raw)}
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            gone = any(c in msg for c in ("403", "404", "Forbidden", "Not Found")) or type(e).__name__ == "GarminConnectNotFoundError"
            out = {"available": False, "readings": [],
                   "reason": "ECG not available from Garmin for this account." if gone else "Couldn't load ECG from Garmin.",
                   "detail": type(e).__name__}
        with self._lock:
            self._cache[k] = (time.time(), out)
        return out

    def days(self, dates: list[str], light: bool = False) -> list[dict]:
        with ThreadPoolExecutor(max_workers=3) as ex:
            return list(ex.map(lambda d: self.day(d, light), dates))


# --------------------------------------------------------------------------- Demo
class DemoSource:
    """Plausible, clearly-labelled sample data. Deterministic per date."""

    mode = "demo"

    def day(self, d: str, light: bool = False) -> dict:
        r = random.Random(d)
        out = _empty_day(d)
        is_today = d == date.today().isoformat()
        out["rhr"] = r.randint(50, 58)
        out["hrv"] = r.randint(48, 78)
        out["hrv_weekly"] = 62
        out["hrv_status"] = "BALANCED"
        out["hrv_base_low"], out["hrv_base_high"] = 52, 74
        total = r.randint(6 * 3600 + 1200, 8 * 3600 + 1800)
        deep, rem = int(total * r.uniform(.14, .22)), int(total * r.uniform(.18, .26))
        awake_s = r.randint(600, 2400)
        start = datetime.fromisoformat(d) + timedelta(hours=r.uniform(6.0, 7.5)) - timedelta(seconds=total + awake_s)
        out["sleep"] = {
            "score": r.randint(58, 92), "total_s": total, "deep_s": deep, "rem_s": rem,
            "light_s": total - deep - rem, "awake_s": awake_s,
            "start_local": start.isoformat(timespec="minutes"),
            "end_local": (start + timedelta(seconds=total + awake_s)).isoformat(timespec="minutes"),
        }
        wake = r.randint(45, 95)
        out["body_battery"] = {"wake": wake, "current": max(5, wake - r.randint(10, 40)), "high": wake, "low": r.randint(5, 25)}
        frac = (datetime.now().hour + datetime.now().minute / 60) / 24 if is_today else 1
        out["steps"] = int(r.randint(6000, 15000) * frac)
        out["step_goal"] = 10000
        out["active_kcal"] = int(r.randint(350, 900) * frac)
        out["moderate_min"] = int(r.randint(10, 50) * frac)
        out["vigorous_min"] = int(r.randint(0, 40) * frac)
        out["stress_avg"] = r.randint(22, 45)
        now = datetime.now()
        at = now - timedelta(minutes=now.minute % 3) if is_today else datetime.fromisoformat(d) + timedelta(hours=23, minutes=57)
        tl, v = [], 18.0
        end_min = (now.hour * 60 + now.minute) if is_today else 24 * 60
        for m in range(0, end_min, 12):
            h = m / 60
            target = 10 if h < 6.5 else 75 if 7 <= h < 8 and out["activities"] else 35 + 20 * (13 <= h < 17)
            v = max(1, min(99, v + (target - v) * 0.35 + r.uniform(-9, 9)))
            tl.append([f"{m // 60:02d}:{m % 60:02d}", int(v)])
        out["stress_timeline"] = tl
        out["stress_bands"] = stress_bands3(tl)
        cnt = {"rest": 0, "low": 0, "medium": 0, "high": 0}
        for _, x in tl:
            cnt["rest" if x <= 25 else "low" if x <= 50 else "medium" if x <= 75 else "high"] += 720
        out["stress_durations"] = cnt
        out["stress_avg"] = int(sum(x for _, x in tl) / len(tl)) if tl else out["stress_avg"]
        out["stress"] = {"avg": out["stress_avg"], "latest": tl[-1][1] if tl else None,
                         "latest_at": at.isoformat(timespec="minutes")}
        out["spo2"] = {"avg": r.randint(95, 98), "latest": r.randint(94, 99), "lowest": r.randint(88, 93),
                       "latest_at": (at - timedelta(minutes=r.randint(5, 50))).isoformat(timespec="minutes")}
        out["skin_temp_dev"] = round(r.uniform(-0.6, 0.6), 1)
        out["total_kcal"] = int(1750 * (frac if is_today else 1)) + out["active_kcal"]
        out["respiration"] = round(r.uniform(13.4, 15.0), 1)
        out["vo2max"] = 49.0 if r.random() < 0.8 else 50.0
        out["sleep_need_s"] = r.randint(7 * 3600 + 1800, 8 * 3600 + 1800)
        # schema-shaped sample night (same structure as Garmin get_sleep_data), parsed by the live parser
        sl_ = out["sleep"]
        raw = demo_sleep_raw(d, sl_["total_s"], sl_["deep_s"], sl_["rem_s"], sl_["awake_s"], start, sl_["score"])
        out["sleep_detail"] = parse_sleep(raw)
        sl_["nap_s"] = out["sleep_detail"]["nap_s"]
        out["sleep_need_s"] = out["sleep_detail"]["need"]["actual_min"] * 60
        out["readiness"] = {"score": r.randint(35, 90), "level": "MODERATE"}
        out["max_hr"], out["min_hr"] = r.randint(140, 178), out["rhr"] - 2
        if r.random() < 0.7:
            kind = r.choice([("Toronto Running", "running", 9500, 152), ("Road Cycling", "road_biking", 38000, 138),
                             ("Strength", "strength_training", 0, 118), ("Lakeshore Ride", "road_biking", 52000, 141)])
            dur = r.randint(35, 95) * 60
            out["activities"].append({
                "name": kind[0], "type": kind[1], "start_local": f"{d}T07:{r.randint(0, 59):02d}",
                "duration_s": dur, "distance_m": kind[2] or None, "avg_hr": kind[3], "max_hr": kind[3] + 22,
                "kcal": dur // 60 * 11, "load": round(dur / 60 * r.uniform(1.2, 2.4)), "aerobic_te": round(r.uniform(2.2, 4.1), 1),
                **dict(zip([f"hr_z{i}" for i in range(1, 6)], _split(r, dur, kind[1]))),
            })
        _finish(out)
        hr = []
        for i in range(96):
            h = i / 4
            base = out["rhr"] + (4 if 0 <= h < 6.5 else 18)
            hr.append(int(base + r.uniform(-4, 6) + (60 if 7 <= h < 8 and out["activities"] else 0)))
        n = int(96 * frac) if is_today else 96
        out["hr_series"] = hr[:max(n, 1)]
        t15 = lambda i: f"{i // 4:02d}:{i % 4 * 15:02d}"
        ser = out["series"]
        ser["hr"] = [[t15(i), v] for i, v in enumerate(out["hr_series"])]
        ser["stress"] = tl
        end_min = (now.hour * 60 + now.minute) if is_today else 24 * 60
        bb, lvl = [], max(5, wake - 70)
        for m in range(0, end_min, 15):
            h = m / 60
            lvl = min(100, lvl + 3.2) if h < 6.5 else max(5, lvl - (2.5 if 7 <= h < 8 and out["activities"] else 0.55))
            bb.append([f"{m // 60:02d}:{m % 60:02d}", int(lvl)])
        ser["bb"] = bb
        if bb:  # keep summary numbers consistent with the generated curve
            vals = [v for _, v in bb]
            at_wake = next((v for t_, v in bb if t_ >= "06:30"), vals[-1])
            out["body_battery"] = _bb_summary(bb, {"wake": at_wake, "current": vals[-1], "high": max(vals), "low": min(vals)})
        night = [m for m in range(-60, 390, 5)]  # 23:00 -> 06:30, 5-min HRV readings
        base_hrv = out["hrv"]
        ser["hrv"] = [[f"{(m % 1440) // 60:02d}:{m % 60:02d}", max(15, int(base_hrv + 14 * math.sin(m / 70) + r.uniform(-9, 9)))] for m in night]
        ser["spo2"] = [[f"{(m % 1440) // 60:02d}:{m % 60:02d}", min(100, max(86, int(round(96 + r.uniform(-3, 2.5)))))] for m in range(-60, 390, 10)]
        ser["resp"] = [[f"{m // 60:02d}:{m % 60:02d}", round((13.2 if m < 390 else 15.5) + r.uniform(-1.2, 1.4) + (9 if 420 <= m < 480 and out["activities"] else 0), 1)]
                       for m in range(0, end_min, 10)]
        out["current_hr"] = {"bpm": out["hr_series"][-1], "at": datetime.now().isoformat(timespec="minutes") if is_today else f"{d}T23:59"}
        if is_today:
            out["training_status"] = {"status": "PRODUCTIVE", "acute_load": 612, "chronic_load": 540}
        return out

    def morning(self, d: str) -> dict:
        """Labelled sample Morning Report readiness, consistent with the demo day's sleep/HRV."""
        day = self.day(d)
        r = random.Random("am" + d)
        sl = (day["sleep"] or {}).get("score") or 70
        sleep_pct = min(100, max(20, sl + r.randint(-5, 8)))
        hrv_pct = r.randint(60, 98)
        load_pct = r.randint(55, 95)
        stress_pct = r.randint(50, 95)
        score = int(round(0.35 * sleep_pct + 0.25 * hrv_pct + 0.2 * load_pct + 0.2 * stress_pct))
        level = "PRIME" if score >= 95 else "HIGH" if score >= 75 else "MODERATE" if score >= 50 else "LOW" if score >= 25 else "POOR"
        short = {"PRIME": "WELL_RECOVERED", "HIGH": "RECOVERED_AND_READY", "MODERATE": "GOOD_SLEEP_LAST_NIGHT",
                 "LOW": "RECOVERING", "POOR": "TAKE_IT_EASY"}[level]
        wake = (day["sleep"] or {}).get("end_local") or f"{d}T06:45"
        return {"readiness": parse_readiness({
            "score": score, "level": level, "feedbackShort": short, "feedbackLong": "GOOD_SLEEP_HISTORY" if sl >= 70 else "POOR_SLEEP",
            "recoveryTime": r.choice([0, 0, 180, 420, 960]), "timestampLocal": wake[:16] + ":00.0",
            "inputContext": "AFTER_WAKEUP_RESET", "sleepScoreFactorPercent": sleep_pct, "hrvFactorPercent": hrv_pct,
            "acwrFactorPercent": load_pct, "stressHistoryFactorPercent": stress_pct}), "error": None}

    def failed_calls(self, dates: list[str]) -> list[str]:
        return []

    def fitness(self, d: str) -> dict:
        """Demo inputs for the Fitness Age screen: 32-year-old whose Garmin Fitness Age drifts down ~0.3 yr/yr
        with small week-to-week noise (so Pace of Aging lands around 0.7x)."""
        t = date.fromisoformat(d)
        hist = []
        for w in range(HISTORY_WEEKS, -1, -1):
            x = t - timedelta(weeks=w)
            yrs = (HISTORY_WEEKS - w) * 7 / 365.25
            jitter = random.Random("fa" + x.isoformat()).uniform(-0.03, 0.03)
            hist.append([x.isoformat(), round(29.6 - 0.3 * yrs + jitter, 2)])
        comps = {"rhr": {"value": 55, "targetValue": 52, "potentialAge": hist[-1][1] - 0.6},
                 "vigorousMinutesAvg": {"value": 88, "targetValue": 150, "potentialAge": hist[-1][1] - 1.2},
                 "bmi": {"value": 23.4, "targetValue": 23.4, "potentialAge": hist[-1][1]}}
        return {"age": 32.0, "sex": "male", "garmin_fitness_age": hist[-1][1], "achievable": 27.5,
                "components": comps, "bmi": 23.4, "body_fat": None, "vo2max": None, "history": hist}

    def ecg(self, d: str) -> dict:
        """Labelled sample ECG readings (with a short synthetic waveform) for demo mode."""
        t = date.fromisoformat(d)
        r = random.Random("ecg" + d)
        rows = []
        for i, (days_ago, hh, cls) in enumerate([(0, "08:12", "Sinus Rhythm"), (3, "21:40", "Sinus Rhythm"),
                                                  (9, "07:05", "Inconclusive"), (16, "12:31", "Sinus Rhythm")]):
            bpm = r.randint(54, 72)
            rows.append({"time": f"{(t - timedelta(days=days_ago)).isoformat()}T{hh}", "classification": cls,
                         "avg_hr": bpm, "duration_s": 30, "samples": _demo_ecg(bpm, r), "sample_rate": 128})
        return {"available": True, "demo": True, "readings": rows}

    def days(self, dates: list[str], light: bool = False) -> list[dict]:
        return [self.day(d) for d in dates]


def _split(r: random.Random, dur: int, kind: str) -> list[int]:
    w = [0.15, 0.35, 0.3, 0.15, 0.05] if kind != "strength_training" else [0.4, 0.4, 0.15, 0.05, 0.0]
    w = [x * r.uniform(0.6, 1.4) for x in w]
    return [int(dur * x / sum(w)) for x in w]


def _demo_ecg(bpm: int, r: random.Random, secs: float = 6.0, hz: int = 128) -> list[float]:
    """Synthetic PQRST-like trace (mV), 6 s at 128 Hz, purely illustrative."""
    out, beat = [], 60 / bpm
    for i in range(int(secs * hz)):
        ph = (i / hz) % beat / beat
        v = 0.12 * math.exp(-((ph - 0.18) / 0.03) ** 2)          # P
        v -= 0.15 * math.exp(-((ph - 0.30) / 0.008) ** 2)        # Q
        v += 1.1 * math.exp(-((ph - 0.32) / 0.010) ** 2)         # R
        v -= 0.25 * math.exp(-((ph - 0.345) / 0.010) ** 2)       # S
        v += 0.28 * math.exp(-((ph - 0.55) / 0.05) ** 2)         # T
        out.append(round(v + r.uniform(-0.02, 0.02), 3))
    return out


def parse_ecg(raw) -> list[dict]:
    """Defensive parser for an ECG list/summary payload of unknown exact shape."""
    items = raw if isinstance(raw, list) else next((raw[k] for k in ("readings", "ecgReadings", "items", "data", "summaries")
                                                    if isinstance(raw, dict) and isinstance(raw.get(k), list)), [])
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        g = lambda *ks: next((it[k] for k in ks if it.get(k) is not None), None)  # noqa: E731
        t = g("startTimeLocal", "readingTimeLocal", "timestampLocal", "startTime", "timestamp")
        rows.append({
            "time": _ts(t) if isinstance(t, (int, float)) else (str(t)[:16] if t else None),
            "classification": str(g("rhythmClassification", "classification", "result", "rhythm") or "Unclassified").replace("_", " ").title().replace("Afib", "AFib"),
            "avg_hr": g("averageHeartRate", "avgHeartRate", "heartRate", "averageHR"),
            "duration_s": g("durationInSeconds", "duration"),
            "samples": g("samples", "voltages", "waveform") if isinstance(g("samples", "voltages", "waveform"), list) else None,
            "sample_rate": g("sampleRate", "samplingFrequency"),
        })
    return sorted(rows, key=lambda x: x["time"] or "", reverse=True)
