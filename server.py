"""Goop server: JSON API + installable PWA.  Run:  python server.py  (or ./run.sh)"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import secrets
import threading
import time
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from goop import fitness, scoring, sleep as sleepmod, tokens, workout as wo, templates as wtpl
from goop.sources import DemoSource, GarminSource, open_from_tokens

logging.basicConfig(level=os.getenv("GOOP_LOG", "INFO"))
log = logging.getLogger("goop")
STATIC = Path(__file__).parent / "static"
ACCESS_KEY = os.getenv("GOOP_ACCESS_KEY")  # optional on the LAN; REQUIRED when GOOP_CLOUD=1
CLOUD = os.getenv("GOOP_CLOUD") == "1"     # public hosting (Render): key on every /api route, no LAN-only check
SERVERLESS = os.getenv("GOOP_SERVERLESS") == "1" or os.getenv("VERCEL") == "1"  # Vercel: stateless, no password sign-in
KEY_COOKIE = "goop_key"
if CLOUD and not ACCESS_KEY:
    logging.getLogger("goop").error("GOOP_CLOUD=1 but GOOP_ACCESS_KEY is not set: every /api route will refuse requests.")


def _pick_source():
    if os.getenv("GOOP_DEMO") == "1":
        return DemoSource(), "GOOP_DEMO=1"
    tokens.migrate_legacy()
    if not (tokens.load() or (os.getenv("GARMIN_EMAIL") and os.getenv("GARMIN_PASSWORD"))):
        return DemoSource(), "Not connected. Connect Garmin in Settings."
    try:
        return GarminSource(), None
    except Exception as e:  # noqa: BLE001  (never includes the password)
        log.warning("Garmin sign-in failed, falling back to demo: %s", type(e).__name__)
        return DemoSource(), f"Garmin sign-in failed ({type(e).__name__}). Reconnect in Settings."


SOURCE, DEMO_REASON = _pick_source()
log.info("Goop data source: %s %s", SOURCE.mode, f"({DEMO_REASON})" if DEMO_REASON else "")

app = FastAPI(title="Goop")


class _RateLimit:
    """Tiny in-memory sliding window per client IP. On Render that's one instance; on Vercel it's per warm
    instance only (best effort: a cold start resets it), which is fine since the access key is the real gate."""
    def __init__(self, n: int, per: float):
        self.n, self.per, self.hits = n, per, {}
        self.lock = threading.Lock()

    def allow(self, who: str) -> bool:
        now = time.time()
        with self.lock:
            h = [t for t in self.hits.get(who, []) if now - t < self.per]
            ok = len(h) < self.n
            if ok:
                h.append(now)
            self.hits[who] = h
            if len(self.hits) > 5000:
                self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < self.per}
            return ok


AUTH_LIMIT = _RateLimit(10, 600)   # key attempts per IP per 10 min
LOGIN_LIMIT = _RateLimit(5, 900)   # Garmin sign-in/MFA attempts per IP per 15 min


def _ip(request: Request) -> str:
    if SERVERLESS and request.headers.get("x-real-ip"):  # Vercel's edge sets x-real-ip to the client IP
        return request.headers["x-real-ip"].strip()
    if CLOUD and request.headers.get("x-forwarded-for"):  # Render's proxy sets this
        return request.headers["x-forwarded-for"].split(",")[-1].strip()  # rightmost = added by Render, not spoofable
    return request.client.host if request.client else "?"


def _key_ok(k) -> bool:
    return bool(ACCESS_KEY and k) and hmac.compare_digest(str(k).encode(), ACCESS_KEY.encode())


@app.middleware("http")
async def access_gate(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path != "/api/auth" and (ACCESS_KEY or CLOUD):
        key = request.headers.get("x-goop-key") or request.cookies.get(KEY_COOKIE)
        if not CLOUD:
            key = key or request.query_params.get("key")  # ?key= only on the LAN (keeps keys out of cloud logs)
        if not _key_ok(key):
            if path == "/api/status" and request.method in ("GET", "HEAD"):  # Render health check: no data, no key
                return JSONResponse({"ok": True, "locked": True})
            if CLOUD and not ACCESS_KEY:
                return JSONResponse({"error": "server misconfigured: set GOOP_ACCESS_KEY"}, status_code=503)
            return JSONResponse({"error": "unauthorized", "need_key": True}, status_code=401)
    resp = await call_next(request)
    if CLOUD:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


class AuthBody(BaseModel):
    key: str = ""


@app.post("/api/auth")
def auth(body: AuthBody, request: Request):
    """Key-entry screen: checks the access key and stores it in an HttpOnly cookie (Secure in the cloud)."""
    if not AUTH_LIMIT.allow(_ip(request)):
        return JSONResponse({"error": "Too many attempts. Wait 10 minutes."}, 429)
    if not ACCESS_KEY:
        return JSONResponse({"error": "This server has no access key configured."}, 503 if CLOUD else 400)
    if not _key_ok(body.key.strip()):
        log.warning("access key rejected")
        return JSONResponse({"error": "That key isn't right."}, 401)
    r = JSONResponse({"ok": True})
    r.set_cookie(KEY_COOKIE, ACCESS_KEY, max_age=365 * 86400, httponly=True, samesite="strict",
                 secure=CLOUD or request.url.scheme == "https", path="/")
    return r


def _score_day(day: dict, history: list[dict]) -> dict:
    hrv_base = scoring.baseline([h["hrv"] for h in history])
    rhr_base = scoring.baseline([h["rhr"] for h in history])
    rec = scoring.recovery_score(
        hrv=day["hrv"], hrv_baseline=hrv_base, rhr=day["rhr"], rhr_baseline=rhr_base,
        sleep_score=(day["sleep"] or {}).get("score"),
        body_battery_wake=(day["body_battery"] or {}).get("wake"),
    )
    strain = scoring.strain_score(activity_load=day["activity_load"], moderate_min=day["moderate_min"],
                                  vigorous_min=day["vigorous_min"], steps=day["steps"] or 0)
    return {"recovery": rec, "strain": strain, "baselines": {"hrv": hrv_base, "rhr": rhr_base}}


@app.get("/api/status")
def status():
    return {"mode": SOURCE.mode, "demo_reason": DEMO_REASON}


FULL_DAYS = 14   # target + 13 prior days fetched in full (trend, baselines, weekly HR zones)
AVG_DAYS = 30    # prior days used for My Dashboard averages (older ones fetched "light")


def _hm(sec):
    if sec is None:
        return None
    sec = int(round(sec))
    return f"{sec // 3600}:{sec % 3600 // 60:02d}"


def _num(v, nd=0):
    if v is None:
        return None
    return f"{v:,.{nd}f}"


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)


def _row(key, label, value, avg, n_avg, better, show, avg_label=None):
    if value is None:
        return None
    direction = good = None
    if avg is not None and show(value) != show(avg):
        direction = "up" if value > avg else "down"
        good = (direction == "up") == (better == "higher")
    return {"key": key, "label": label, "value": value, "display": show(value),
            "avg": avg, "avg_display": show(avg) if avg is not None else None,
            "avg_label": avg_label or (f"{n_avg}-day avg" if n_avg else None), "dir": direction, "good": good}


def _my_dashboard(days: list[dict]) -> list[dict]:
    t, prior = days[-1], days[:-1]
    full_prior = [d for d in prior if not d["light"]]
    rows = []

    def add(key, label, field, better, show, pool=None):
        pool = prior if pool is None else pool
        a, n = _avg([d[field] for d in pool])
        rows.append(_row(key, label, t[field], a, n, better, show))

    add("steps", "Steps", "steps", "higher", lambda v: _num(v))
    add("rhr", "Resting Heart Rate", "rhr", "lower", lambda v: _num(v))
    add("hrv", "Heart Rate Variability", "hrv", "higher", lambda v: _num(v))
    add("vo2max", "VO₂ Max", "vo2max", "higher", lambda v: _num(v), full_prior)
    add("resp", "Respiratory Rate", "respiration", "lower", lambda v: _num(v, 1))
    strength = t["strength_s"] or any(d["strength_s"] for d in full_prior)
    if t["activities"] or any(d["activities"] for d in full_prior):  # skip when Garmin has no workouts at all
        add("activity_time", "Strength Activity Time" if strength else "Activity Time",
            "strength_s" if strength else "activity_s", "higher", _hm, full_prior)

    # weekly HR zones: this 7-day window vs the 7 days before it
    def zsum(ds, lo, hi):
        zs = [d["zone_s"] for d in ds if d["zone_s"]]
        return sum(sum(z[lo:hi]) for z in zs) if zs else None
    this_wk, prev_wk = days[-7:], [d for d in days[-14:-7] if not d["light"]]
    if any(d["zone_s"] for d in this_wk + prev_wk) or any(d["activities"] for d in this_wk):
        for key, label, lo, hi in (("z13", "HR Zones 1-3 (Weekly)", 0, 3), ("z45", "HR Zones 4-5 (Weekly)", 3, 5)):
            v = zsum(this_wk, lo, hi) or 0
            p = zsum(prev_wk, lo, hi) if len(prev_wk) == 7 else None
            rows.append(_row(key, label, v, p, 0, "higher", _hm, "prev week" if p is not None else None))
    add("kcal", "Calories", "total_kcal", "higher", lambda v: _num(v))
    return [r for r in rows if r]


def _stats(vals):
    vals = [v for v in vals if v is not None]
    if len(vals) < 3:
        return None
    m = sum(vals) / len(vals)
    sd = (sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5
    return m, sd, len(vals)


def _health_monitor(t: dict, prior: list[dict]) -> dict:
    """WHOOP-style Health Monitor: each vital vs a normal range from his own baseline
    (mean ± max(2·SD, a minimum band)) over up to 30 prior days."""
    spo2 = lambda d: (d["spo2"] or {}).get("avg")
    specs = [  # key, label, unit, getter, min half-band, decimals, direction that is ever "out" ("both"/"low")
        ("resp", "Respiratory Rate", "rpm", lambda d: d["respiration"], 1.0, 1, "both"),
        ("spo2", "Blood Oxygen", "%", spo2, 2.0, 0, "low"),
        ("rhr", "Resting Heart Rate", "bpm", lambda d: d["rhr"], 3.0, 0, "both"),
        ("hrv", "Heart Rate Variability", "ms", lambda d: d["hrv"], None, 0, "both"),
        ("skin", "Skin Temperature", "°C", lambda d: d["skin_temp_dev"], 0.5, 1, "both"),
    ]
    rows = []
    for key, label, unit, get, band, nd, kind in specs:
        v, st = get(t), _stats([get(d) for d in prior])
        row = {"key": key, "label": label, "unit": unit, "value": v, "recorded": v is not None,
               "low": None, "high": None, "ok": None, "baseline_days": st[2] if st else 0}
        if st:
            m, sd, _ = st
            half = max(2 * sd, band if band is not None else 0.15 * m)
            lo, hi = m - half, (100.0 if kind == "low" and key == "spo2" else m + half)
            row.update(low=round(lo, nd), high=round(min(hi, 100) if key == "spo2" else hi, nd))
            if v is not None:
                row["ok"] = bool(row["low"] <= round(v, nd) <= row["high"]) if kind == "both" else bool(round(v, nd) >= row["low"])
        row["series"] = {"resp": "resp", "spo2": "spo2", "hrv": "hrv"}.get(key)  # intraday series key, else 7-day trend
        row["series_note"] = "overnight" if key == "hrv" else "overnight / all-day" if key == "spo2" else None
        row["trend"] = [[d["date"], get(d)] for d in (prior + [t])[-7:]]
        rows.append(row)
    judged = [r for r in rows if r["ok"] is not None]
    ok = sum(r["ok"] for r in judged)
    return {"ok": ok, "total": len(judged), "within_range": bool(judged) and ok == len(judged),
            "current_hr": t.get("current_hr"), "metrics": rows}


def _hr_hrv(days: list[dict]) -> dict:
    """Aligned per-day RHR + overnight HRV (with Garmin's balanced baseline band) for the combined chart."""
    series = [{"date": d["date"], "rhr": d["rhr"], "hrv": d["hrv"], "hrv_weekly": d.get("hrv_weekly"),
               "hrv_baseline_low": d.get("hrv_base_low"), "hrv_baseline_high": d.get("hrv_base_high"),
               "status": d.get("hrv_status")} for d in days]
    t, prior = series[-1], series[:-1]
    rhr_avg, _ = _avg([p["rhr"] for p in prior])
    lo = next((p["hrv_baseline_low"] for p in reversed(series) if p["hrv_baseline_low"]), None)
    hi = next((p["hrv_baseline_high"] for p in reversed(series) if p["hrv_baseline_high"]), None)
    hrv_state = rhr_state = None
    hrv_avg, n_hrv = _avg([p["hrv"] for p in prior])
    hrv_ref = "baseline"
    if t["hrv"] is not None and lo and hi:
        hrv_state = "above" if t["hrv"] > hi else "below" if t["hrv"] < lo else "within"
    elif t["hrv"] is not None and n_hrv >= 2:  # Garmin baseline still building: compare with his own recent average
        hrv_ref = "average"
        hrv_state = "above" if t["hrv"] > hrv_avg * 1.07 else "below" if t["hrv"] < hrv_avg * 0.93 else "within"
    if t["rhr"] is not None and rhr_avg is not None:
        diff = t["rhr"] - rhr_avg
        rhr_state = "above" if diff >= 2 else "below" if diff <= -2 else "near"
    insight = tone = None
    if hrv_state and rhr_state:
        h = {"above": f"HRV above {hrv_ref}", "within": f"HRV {'within baseline' if hrv_ref == 'baseline' else 'near average'}",
             "below": f"HRV below {hrv_ref}"}[hrv_state]
        r = {"above": "RHR above average", "below": "RHR below average", "near": "RHR near average"}[rhr_state]
        if hrv_state == "below" and rhr_state == "above":
            v, tone = "body under strain, take it easy", "red"
        elif hrv_state == "below" or rhr_state == "above":
            v, tone = "some strain showing, moderate effort", "yellow"
        elif hrv_state == "above" or rhr_state == "below":
            v, tone = "well recovered", "green"
        else:
            v, tone = "steady, recovering normally", "green"
        insight = f"{h}, {r}: {v}"
    elif t["rhr"] is not None and rhr_state:
        insight, tone = f"RHR {rhr_state} your average" + ("" if t["hrv"] is not None else " (no overnight HRV yet)"), None
    return {"series": series, "rhr_avg": round(rhr_avg, 1) if rhr_avg is not None else None,
            "hrv_avg": round(hrv_avg, 1) if hrv_avg is not None else None, "hrv_ref": hrv_ref,
            "baseline_low": lo, "baseline_high": hi, "today": t, "insight": insight, "tone": tone}


def _stress_monitor(t: dict) -> dict | None:
    s = t.get("stress") or {}
    v = s.get("latest") if s.get("latest") is not None else s.get("avg")
    if v is None:
        return None
    # WHOOP-style 0-3 scale (= Garmin 0-100 * 3/100): LOW 0-<1, MEDIUM 1-<2, HIGH 2-3
    x = round(v * 3 / 100, 1)
    level = "LOW" if x < 1 else "MEDIUM" if x < 2 else "HIGH"
    return {"value": v, "level": level, "at": s.get("latest_at"), "avg": s.get("avg"),
            "whoop_scale": x, "bands": t.get("stress_bands"),  # seconds per 0-3 band
            "source": "latest" if s.get("latest") is not None else "daily average",
            "timeline": t.get("stress_timeline") or [], "durations": t.get("stress_durations"),
            "body_battery": t.get("body_battery")}


def _tonight(t: dict, prior7: list[dict], is_today: bool) -> dict | None:
    if not is_today or not t.get("sleep_need_s"):
        return None
    wakes = [d["sleep"]["end_local"][11:16] for d in prior7 + [t] if d["sleep"] and d["sleep"].get("end_local")]
    if not wakes:
        return {"need_s": t["sleep_need_s"], "bedtime": None, "wake": None}
    mins = sum(int(w[:2]) * 60 + int(w[3:]) for w in wakes) // len(wakes)
    bed = (mins - t["sleep_need_s"] // 60) % 1440
    return {"need_s": t["sleep_need_s"], "wake": f"{mins // 60:02d}:{mins % 60:02d}", "bedtime": f"{bed // 60:02d}:{bed % 60:02d}"}


@app.get("/api/dashboard")
def dashboard(day: str | None = None):
    try:
        target = date.fromisoformat(day) if day else date.today()
    except ValueError:
        raise HTTPException(400, "day must be YYYY-MM-DD")
    if target > date.today() + timedelta(days=1):  # 1-day grace: phones ahead of UTC
        raise HTTPException(400, "day is in the future")
    dates = [(target - timedelta(days=i)).isoformat() for i in range(AVG_DAYS, -1, -1)]
    n_light = len(dates) - FULL_DAYS
    days = SOURCE.days(dates[:n_light], light=True) + SOURCE.days(dates[n_light:])
    recent = days[n_light:]  # FULL_DAYS full records, last one is target
    trend = []
    for i in range(7, FULL_DAYS):
        s = _score_day(recent[i], recent[i - 7:i])
        trend.append({
            "date": recent[i]["date"], "recovery": s["recovery"]["score"], "strain": s["strain"]["score"],
            "sleep_h": round((recent[i]["sleep"] or {}).get("total_s", 0) / 3600, 1) or None,
            "hrv": recent[i]["hrv"], "rhr": recent[i]["rhr"],
        })
    today = recent[-1]
    prior7 = recent[-8:-1]
    for a in today["activities"]:
        a["strain"] = scoring.strain_score(activity_load=a.get("load") or 0, moderate_min=0, vigorous_min=0, steps=0)["score"] \
            if a.get("load") is not None else None
    s = _score_day(today, prior7)
    return {
        "mode": SOURCE.mode, "demo_reason": DEMO_REASON, "date": today["date"],
        "is_today": target == date.today(),
        "recovery": s["recovery"], "strain": s["strain"], "baselines": s["baselines"],
        "recommended_strain": scoring.recommended_strain(s["recovery"]["score"]),
        "health_monitor": _health_monitor(today, days[:-1]),
        "stress_monitor": _stress_monitor(today),
        "hr_hrv": _hr_hrv(days),
        "tonight": _tonight(today, prior7, target == date.today()),
        "my_dashboard": _my_dashboard(days),
        "today": today, "trend": trend,
    }


@app.get("/api/fitness")
def fitness_age(day: str | None = None):
    try:
        target = date.fromisoformat(day) if day else date.today()
    except ValueError:
        raise HTTPException(400, "day must be YYYY-MM-DD")
    if target > date.today() + timedelta(days=1):  # 1-day grace: phones ahead of UTC
        raise HTTPException(400, "day is in the future")
    dates = [(target - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    days = SOURCE.days(dates)  # same full-day cache the dashboard uses
    out = fitness.build(SOURCE.fitness(target.isoformat()), days, target, SOURCE.mode)
    out["demo_reason"] = DEMO_REASON
    return out


@app.get("/api/ecg")
def ecg(day: str | None = None):
    """ECG readings if Garmin exposes them; otherwise the closest metrics (resting HR, overnight HRV)."""
    try:
        target = date.fromisoformat(day) if day else date.today()
    except ValueError:
        raise HTTPException(400, "day must be YYYY-MM-DD")
    out = SOURCE.ecg(target.isoformat())
    if not out.get("readings"):
        dates = [(target - timedelta(days=i)).isoformat() for i in range(7, -1, -1)]
        days = SOURCE.days(dates)
        t, prior = days[-1], days[:-1]
        out["fallback"] = {
            "rhr": t["rhr"], "rhr_7d": scoring.baseline([d["rhr"] for d in prior]),
            "hrv": t["hrv"], "hrv_7d": scoring.baseline([d["hrv"] for d in prior]), "hrv_status": t.get("hrv_status"),
        }
    out["mode"] = SOURCE.mode
    return out


# ---------------------------------------------------------------- Sleep (home coach card + #sleep)
@app.get("/api/sleep")
def sleep_api(day: str | None = None):
    """Last night in detail (Garmin get_sleep_data, parsed) + 7-night history + Goop's sleep coach."""
    try:
        target = date.fromisoformat(day) if day else date.today()
    except ValueError:
        raise HTTPException(400, "day must be YYYY-MM-DD")
    if target > date.today() + timedelta(days=1):  # 1-day grace: phones ahead of UTC
        raise HTTPException(400, "day is in the future")
    dates = [(target - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    days = SOURCE.days(dates)  # per-day calls, cached by the source (same cache as the dashboard)
    nights = [d.get("sleep_detail") for d in days]
    last = nights[-1]
    history = []
    for d, n in zip(days, nights):
        s = d.get("sleep") or {}
        need = ((n or {}).get("need") or {}).get("actual_min") or (d["sleep_need_s"] // 60 if d.get("sleep_need_s") else None)
        history.append({"date": d["date"], "score": (n or {}).get("score") or s.get("score"),
                        "total_s": (n or {}).get("total_s") or s.get("total_s"), "nap_s": (n or {}).get("nap_s") or s.get("nap_s") or 0,
                        "need_min": need, "qualifier": (n or {}).get("qualifier")})
    tonight = (last or {}).get("next_need")
    c = sleepmod.coach(nights, tonight)
    c["goop_computed"] = ["bedtime", "debt_s", "consistency"]
    return {"mode": SOURCE.mode, "date": target.isoformat(), "is_today": target == date.today(),
            "night": last, "history": history, "coach": c}


# ---------------------------------------------------------------- Morning Report
PARTIAL_NOTE = ("Some Garmin data didn't load. Goop uses an unofficial Garmin connection "
                "that hasn't been verified with your account yet.")
_MORNING_CALLS = {"get_stats", "get_sleep_data", "get_hrv_data", "get_heart_rates", "get_body_battery", "get_training_status"}


def human(code: str | None) -> str | None:
    """'RECOVERED_AND_READY' -> 'Recovered and ready'; 'PRODUCTIVE_3' -> 'Productive'; HRV/ACWR kept upper-case."""
    if not code or not isinstance(code, str):
        return None
    words = [w for w in code.strip().split("_") if w and not w.isdigit()]
    if not words:
        return None
    keep = {"HRV", "ACWR", "VO2", "AM", "PM"}
    out = [w if w in keep else w.lower() for w in words]
    out[0] = out[0] if out[0] in keep else out[0].capitalize()
    return " ".join(out)


def _dur(sec):
    if not sec:
        return None
    m = int(round(sec / 60))
    return f"{m // 60}h {m % 60:02d}m"


def _morning_summary(rd: dict | None, m: dict) -> str | None:
    """One plain sentence built only from the numbers we actually have."""
    parts = []
    if m["sleep_s"]:
        parts.append(f"You slept {_dur(m['sleep_s'])}" + (f" (sleep score {m['sleep_score']})" if m["sleep_score"] is not None else ""))
    if m["bb_wake"] is not None:
        parts.append(f"woke with Body Battery at {m['bb_wake']}")
    if m["hrv"] is not None:
        st = human(m["hrv_status"])
        parts.append(f"overnight HRV was {m['hrv']} ms" + (f" ({st.lower()})" if st else ""))
    if parts:
        parts[0] = "You " + parts[0] if parts[0].startswith("woke") else parts[0][0].upper() + parts[0][1:]
    s = ""
    if parts:
        s = parts[0] + (", " + ", ".join(parts[1:-1]) if len(parts) > 2 else "") + (" and " + parts[-1] if len(parts) > 1 else "") + ". "
    if rd and rd.get("score") is not None:
        lvl = (rd.get("level") or "").upper()
        advice = {"PRIME": "it's a good day for a hard session", "HIGH": "it's a good day for a hard session",
                  "MODERATE": "steady training is fine, but keep the intensity in check",
                  "LOW": "keep it easy today", "POOR": "prioritise rest today"}.get(lvl)
        s += f"Readiness is {rd['score']}" + (f" ({human(lvl).lower()})" if lvl else "") + (f", so {advice}" if advice else "") + "."
        rec = rd.get("recovery_min")
        if rec and rd.get("recovery_phrase") != "REACHED_ZERO":
            s += f" Garmin still counts {round(rec / 60)} h of recovery time."
    return s.strip() or None


@app.get("/api/morning")
def morning(day: str | None = None):
    """Morning Report: Garmin's after-wake-up Training Readiness + last night + yesterday's totals."""
    try:
        target = date.fromisoformat(day) if day else date.today()
    except ValueError:
        raise HTTPException(400, "day must be YYYY-MM-DD")
    if target > date.today() + timedelta(days=1):  # 1-day grace: phones ahead of UTC
        raise HTTPException(400, "day is in the future")
    d, y = target.isoformat(), (target - timedelta(days=1)).isoformat()
    yday, t = SOURCE.days([y, d])
    mr = SOURCE.morning(d)
    rd = mr.get("readiness")
    if rd:
        rd = {**rd, "level_text": human(rd.get("level")), "feedback_text": human(rd.get("feedback_short")),
              "feedback_long_text": human(rd.get("feedback_long")),
              "factors": [{**f, "feedback_text": human(f.get("feedback"))} for f in rd["factors"]]}
    sl, bb, ts = t.get("sleep") or {}, t.get("body_battery") or {}, t.get("training_status") or {}
    metrics = {
        "sleep_score": sl.get("score"), "sleep_s": sl.get("total_s"), "sleep_need_s": t.get("sleep_need_s"),
        "hrv": t.get("hrv"), "hrv_status": t.get("hrv_status"), "hrv_weekly": t.get("hrv_weekly"),
        "bb_wake": bb.get("wake"), "rhr": t.get("rhr"),
        "training_status": human(ts.get("status")), "acute_load": ts.get("acute_load"),
        "yesterday": {"date": y, "steps": yday.get("steps"), "step_goal": yday.get("step_goal"),
                      "kcal": yday.get("total_kcal"), "active_kcal": yday.get("active_kcal")},
    }
    failed = [n for n in SOURCE.failed_calls([d, y]) if n in _MORNING_CALLS]
    if mr.get("error"):
        failed = ["get_training_readiness"] + failed
    partial = SOURCE.mode == "garmin" and bool(failed)
    return {"mode": SOURCE.mode, "date": d, "readiness": rd, "metrics": metrics,
            "summary": _morning_summary(rd, metrics), "partial": partial,
            "note": PARTIAL_NOTE if partial else None, "failed": failed}


# ---------------------------------------------------------------- Garmin sign-in (LAN only)
# The password is used for exactly one login call and never stored or logged; only OAuth tokens are kept.
_PENDING: dict[str, dict] = {}       # MFA in progress: id -> {"api", "email", "expires"}
_SWAP = threading.Lock()


def _client_is_private(request: Request) -> bool:
    hosts = [request.client.host if request.client else ""]
    for h in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):  # tunnels/proxies arrive via localhost
        if request.headers.get(h):
            hosts.append(request.headers[h].split(",")[0].strip())
    try:
        return all(ipaddress.ip_address(h).is_private or ipaddress.ip_address(h).is_loopback for h in hosts)
    except ValueError:
        return False


def _lan_only(request: Request):
    if CLOUD:  # public host: the access key (checked in access_gate) replaces the home-network check
        return
    if not _client_is_private(request):
        raise HTTPException(403, "Garmin sign-in is only available from your home network.")


def _set_source(src, reason):
    global SOURCE, DEMO_REASON
    with _SWAP:
        SOURCE, DEMO_REASON = src, reason  # new source = fresh, empty caches


def _status():
    s = SOURCE
    live = s.mode == "garmin"
    return {"connected": live, "mode": s.mode, "name": getattr(s, "name", None) if live else None,
            "email": getattr(s, "email", None) if live else None,
            "last_sync": getattr(s, "last_sync", None) if live else None,
            "token_store": tokens.backend(), "demo_reason": DEMO_REASON, "cloud": CLOUD,
            "serverless": SERVERLESS, "signin_available": not SERVERLESS}


def _finish_login(api, email: str):
    """Swap to live data using only the OAuth tokens from this login."""
    tok = api.client.dumps()
    live_api = open_from_tokens(tok)
    tokens.save(tok, email=email, name=getattr(live_api, "full_name", None) or getattr(live_api, "display_name", None))
    _set_source(GarminSource(api=live_api), None)
    log.info("Garmin connected; data source is now live (tokens in %s)", tokens.backend())
    return {"status": "connected", **_status()}


def _login_error(e: Exception):
    from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError
    log.warning("Garmin sign-in failed: %s", type(e).__name__)  # type only: never the request body
    if isinstance(e, GarminConnectTooManyRequestsError):
        return JSONResponse({"status": "error", "error": "Garmin is rate-limiting sign-ins. Wait a few minutes and try again."}, 429)
    if isinstance(e, GarminConnectAuthenticationError):
        return JSONResponse({"status": "error", "error": "Garmin rejected that email or password (or code)."}, 401)
    return JSONResponse({"status": "error", "error": "Couldn't reach Garmin Connect. Try again, or use setup_garmin.py."}, 502)


class LoginBody(BaseModel):
    email: str
    password: str

    def __repr__(self):  # keep the password out of any accidental repr/log
        return f"LoginBody(email={self.email!r}, password=***)"
    __str__ = __repr__


class MfaBody(BaseModel):
    pending_id: str
    code: str


@app.get("/api/garmin/status")
def garmin_status(request: Request):
    _lan_only(request)
    return _status()


def _no_serverless_login():
    """Password + MFA sign-in needs state across requests (pending MFA) and writable token storage,
    neither of which exists on serverless. Tokens come from GOOP_GARMIN_TOKENS (export_tokens.py) instead."""
    if SERVERLESS:
        return JSONResponse({"status": "error", "error": "Sign-in isn't available on the hosted Goop. Connect Garmin "
                             "on your Mac, run export_tokens.py and update GOOP_GARMIN_TOKENS in Vercel."}, 501)
    return None


def _login_limited(request: Request):
    if not LOGIN_LIMIT.allow(_ip(request)):
        log.warning("Garmin sign-in rate-limited")
        return JSONResponse({"status": "error", "error": "Too many sign-in attempts. Wait 15 minutes."}, 429)
    return None


@app.get("/api/garmin/tokens")
def garmin_tokens(request: Request):
    """Whether Garmin tokens were refreshed in memory (cloud) and where they're stored. Never returns tokens."""
    _lan_only(request)
    return tokens.refresh_info()


@app.post("/api/garmin/login")
def garmin_login(body: LoginBody, request: Request):
    _lan_only(request)
    if (r := _no_serverless_login()):
        body.password = ""
        return r
    if (r := _login_limited(request)):
        body.password = ""
        return r
    from garminconnect import Garmin
    email = body.email.strip()
    if not email or not body.password:
        return JSONResponse({"status": "error", "error": "Enter your Garmin email and password."}, 400)
    for k, v in list(_PENDING.items()):
        if v["expires"] < time.time():
            _PENDING.pop(k, None)
    api = Garmin(email=email, password=body.password, return_on_mfa=True)
    try:
        mfa, _ = api.login()
    except Exception as e:  # noqa: BLE001
        return _login_error(e)
    finally:
        api.password = None  # drop our reference to the password as soon as the login call returns
        body.password = ""
    if mfa == "needs_mfa":
        pid = secrets.token_urlsafe(18)
        _PENDING[pid] = {"api": api, "email": email, "expires": time.time() + 600}
        return {"status": "needs_mfa", "pending_id": pid}
    try:
        return _finish_login(api, email)
    except Exception as e:  # noqa: BLE001
        return _login_error(e)


@app.post("/api/garmin/mfa")
def garmin_mfa(body: MfaBody, request: Request):
    _lan_only(request)
    if (r := _no_serverless_login()):
        return r
    if (r := _login_limited(request)):
        return r
    p = _PENDING.get(body.pending_id)
    if not p or p["expires"] < time.time():
        _PENDING.pop(body.pending_id, None)
        return JSONResponse({"status": "error", "error": "Sign-in expired. Start again."}, 410)
    try:
        p["api"].resume_login(None, body.code.strip())
    except Exception as e:  # noqa: BLE001  (wrong code leaves the MFA session open for a retry)
        return _login_error(e)
    _PENDING.pop(body.pending_id, None)
    try:
        return _finish_login(p["api"], p["email"])
    except Exception as e:  # noqa: BLE001
        return _login_error(e)


@app.post("/api/garmin/logout")
def garmin_logout(request: Request):
    _lan_only(request)
    tokens.delete()
    _PENDING.clear()
    _set_source(DemoSource(), "Not connected. Connect Garmin in Settings.")
    log.info("Garmin disconnected; tokens deleted; demo mode")
    return {"status": "disconnected", **_status()}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


# ---------------------------------------------------------------- Send workout to watch (LAN only, confirm required)
UNOFFICIAL_NOTE = ("Goop uses an unofficial Garmin connection that hasn't been verified with your account yet. "
                   "If something goes wrong, check Garmin Connect → Training → Workouts.")


class WorkoutSend(BaseModel):
    token: str = ""
    confirm: bool = False


@app.get("/api/templates")
def workout_templates():
    """Static Goop workout library (no Garmin call)."""
    return {"goals": [{"key": g, "label": wtpl.GOAL_LABELS[g]} for g in wtpl.GOALS], "templates": wtpl.TEMPLATES}


@app.post("/api/workout/preview")
def workout_preview(spec: dict, request: Request):
    """Validate + build the Garmin workout locally (no Garmin call) and issue a single-use 10-min token."""
    try:
        p = wo.preview(spec)
    except wo.WorkoutError as e:
        raise HTTPException(400, str(e))
    live = SOURCE.mode == "garmin"
    return {**p, "can_send": live and (CLOUD or _client_is_private(request)), "mode": SOURCE.mode,
            "account": (getattr(SOURCE, "name", None) or getattr(SOURCE, "email", None)) if live else None,
            "blocked_reason": None if live else "Demo mode: connect Garmin in Settings to send workouts.",
            "note": UNOFFICIAL_NOTE}


@app.post("/api/workout/send")
def workout_send(body: WorkoutSend, request: Request):
    """Upload -> schedule -> push, only with a fresh preview token AND confirm:true. Reports partial success."""
    _lan_only(request)
    if SOURCE.mode != "garmin":
        raise HTTPException(409, "Sending is disabled in demo mode. Connect Garmin in Settings first.")
    if body.confirm is not True:
        raise HTTPException(400, "Confirmation required (confirm: true).")
    p = wo.take(body.token)
    if not p:
        raise HTTPException(410, "This preview expired or was already sent. Review the workout again.")
    res = wo.send(SOURCE.api, p)
    log.info("workout send: %s (failed step: %s, %s)", res["status"], res.get("failed_step"), res.get("error"))
    labels = {"upload": "save it to Garmin Connect", "schedule": f"schedule it for {p['workout']['date']}", "push": "push it to your watch"}
    if res["status"] == "ok":
        res["message"] = f"Sent “{p['workout']['name']}” to Garmin" + (" and your watch." if p["workout"]["push"] else f", scheduled for {p['workout']['date']}.")
        return res
    did = {"upload": "Saved to Garmin Connect", "schedule": f"scheduled for {p['workout']['date']}", "push": "pushed to watch"}
    done_txt = ", ".join(did[d["step"]] for d in res["done"])
    res["message"] = (f"{done_txt}, but couldn't {labels[res['failed_step']]} ({res['error']}). " if res["done"] else
                      f"Couldn't {labels[res['failed_step']]} ({res['error']}). Nothing was sent. ") + (
                      "Don't resend (that would create a duplicate): finish it in Garmin Connect → Training → Workouts." if res["done"] else "You can try again.")
    res["note"] = UNOFFICIAL_NOTE
    return JSONResponse(res, status_code=207 if res["status"] == "partial" else 502)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/sw.js")  # served from root so its scope covers the whole app
def sw():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("GOOP_HOST", "0.0.0.0"), port=int(os.getenv("PORT") or os.getenv("GOOP_PORT", "8787")),
                proxy_headers=CLOUD, forwarded_allow_ips="*" if CLOUD else None)
