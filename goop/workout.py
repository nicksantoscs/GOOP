"""'Send workout to watch': validate a simple structured workout, build Garmin Connect workout JSON with the
garminconnect helpers, and send it (upload -> schedule on a date -> optionally push to the last-used watch).

Nothing here talks to Garmin until send() is called with a confirmed, server-issued preview token."""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from datetime import date, timedelta

from garminconnect.workout import (CyclingWorkout, RunningWorkout, WorkoutSegment, create_cooldown_step,
                                   create_distance_interval_step, create_interval_step, create_recovery_step, create_repeat_group, create_warmup_step)

SPORTS = {"running": (RunningWorkout, {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}),
          "cycling": (CyclingWorkout, {"sportTypeId": 2, "sportTypeKey": "cycling", "displayOrder": 2})}
HR_ZONE = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 1}
TOKEN_TTL = 600
_PREVIEWS: dict[str, dict] = {}
_LOCK = threading.Lock()


class WorkoutError(ValueError):
    pass


def validate(spec: dict) -> dict:
    """Normalise + bound-check the builder form. Raises WorkoutError with a user-facing message."""
    def num(k, lo, hi, default=None, integer=False):
        v = spec.get(k, default)
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise WorkoutError(f"{k.replace('_', ' ')} must be a number")
        if not lo <= v <= hi:
            raise WorkoutError(f"{k.replace('_', ' ')} must be between {lo:g} and {hi:g}")
        return int(v) if integer else v
    sport = spec.get("sport", "running")
    if sport not in SPORTS:
        raise WorkoutError("sport must be running or cycling")
    name = " ".join(str(spec.get("name") or "").split())[:60] or None
    def zn(z):
        if z in (None, "", "none", 0, "0"):
            return None
        try:
            z = int(z)
        except (TypeError, ValueError):
            raise WorkoutError("zone must be 1-5")
        if not 1 <= z <= 5:
            raise WorkoutError("zone must be between 1 and 5")
        return z
    raw = spec.get("parts")
    if raw is None:  # legacy single-part form (update8)
        raw = [{"v": spec.get("work_min", 3), "u": "min", "z": spec.get("zone")}]
    if not isinstance(raw, list) or not 1 <= len(raw) <= 6:
        raise WorkoutError("a workout needs 1 to 6 work parts")
    parts = []
    for i, p in enumerate(raw, 1):
        u = (p or {}).get("u", "min")
        if u not in ("min", "m"):
            raise WorkoutError(f"part {i}: unit must be min or m")
        if u == "m" and sport != "running":
            raise WorkoutError("distance parts are for runs only")
        try:
            v = float(p.get("v"))
        except (TypeError, ValueError):
            raise WorkoutError(f"part {i}: value must be a number")
        lo, hi = (0.25, 240) if u == "min" else (50, 42195)
        if not lo <= v <= hi:
            raise WorkoutError(f"part {i}: must be between {lo:g} and {hi:g} {u}")
        parts.append({"v": v, "u": u, "z": zn(p.get("z"))})
    zone = parts[0]["z"]
    try:
        when = date.fromisoformat(str(spec.get("date") or date.today().isoformat()))
    except ValueError:
        raise WorkoutError("date must be YYYY-MM-DD")
    if not date.today() <= when <= date.today() + timedelta(days=90):
        raise WorkoutError("date must be today or within the next 90 days")
    out = {"sport": sport, "warmup_min": num("warmup_min", 0, 60, 10), "repeats": num("repeats", 1, 30, 5, integer=True),
           "parts": parts, "recovery_min": num("recovery_min", 0, 30, 2), "zone": zone,
           "cooldown_min": num("cooldown_min", 0, 60, 10), "date": when.isoformat(), "push": bool(spec.get("push", True))}
    work = sum(part_min(p) for p in parts)
    total = out["warmup_min"] + out["repeats"] * (work + out["recovery_min"]) + out["cooldown_min"]
    if total > 300:
        raise WorkoutError("workout is longer than 5 hours")
    z = f" @ Z{zone}" if zone else ""
    out["name"] = name or (f"Goop {out['repeats']}×{fmt_part(parts[0])}{z}" if len(parts) == 1 else f"Goop {out['repeats']}× mixed")
    out["estimated"] = any(p["u"] == "m" for p in parts)
    out["total_min"] = round(total, 1)
    return out


# rough pace for duration estimates of distance parts (min/km by HR zone); Garmin ends those steps by distance anyway
_PACE = {None: 5.5, 1: 6.5, 2: 6.0, 3: 5.3, 4: 4.8, 5: 4.3}


def part_min(p: dict) -> float:
    return p["v"] if p["u"] == "min" else p["v"] / 1000 * _PACE[p["z"]]


def fmt_part(p: dict) -> str:
    if p["u"] == "m":
        return f"{p['v']:g} m"
    return f"{p['v'] * 60:g} s" if p["v"] < 1 else f"{p['v']:g} min"


def build(w: dict) -> dict:
    """Garmin Connect workout JSON (same shape the official web builder posts)."""
    cls, sport_type = SPORTS[w["sport"]]
    steps, order = [], 1
    if w["warmup_min"]:
        steps.append(create_warmup_step(w["warmup_min"] * 60, order)); order += 1
    rep_order = order; order += 1
    inner = []
    for p in w["parts"]:
        tgt = {"target_type": HR_ZONE, "zone_number": p["z"]} if p["z"] else {}
        inner.append(create_distance_interval_step(p["v"], order, **tgt) if p["u"] == "m"
                     else create_interval_step(p["v"] * 60, order, **tgt))
        order += 1
    if w["recovery_min"]:
        inner.append(create_recovery_step(w["recovery_min"] * 60, order)); order += 1
    steps.append(create_repeat_group(w["repeats"], inner, rep_order))
    if w["cooldown_min"]:
        steps.append(create_cooldown_step(w["cooldown_min"] * 60, order))
    wk = cls(workoutName=w["name"], estimatedDurationInSecs=int(w["total_min"] * 60),
             workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=sport_type, workoutSteps=steps)],
             description="Created with Goop")
    return wk.to_dict()


def summary(w: dict) -> list[str]:
    work = " → ".join(fmt_part(p) + (f" Z{p['z']}" if p["z"] else "") for p in w["parts"])
    if len(w["parts"]) == 1 and w["parts"][0]["z"]:
        work = fmt_part(w["parts"][0]) + f" in HR zone {w['parts'][0]['z']}"
    lines = []
    if w["warmup_min"]:
        lines.append(f"Warm-up {w['warmup_min']:g} min")
    lines.append((f"{w['repeats']} × " if w["repeats"] > 1 else "") + work + (f", {w['recovery_min']:g} min recovery" if w["recovery_min"] else ""))
    if w["cooldown_min"]:
        lines.append(f"Cool-down {w['cooldown_min']:g} min")
    return lines


def _digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def preview(spec: dict) -> dict:
    w = validate(spec)
    payload = build(w)
    tok = secrets.token_urlsafe(18)
    now = time.time()
    with _LOCK:
        for k in [k for k, v in _PREVIEWS.items() if v["expires"] < now]:
            _PREVIEWS.pop(k, None)
        _PREVIEWS[tok] = {"workout": w, "payload": payload, "digest": _digest(payload), "expires": now + TOKEN_TTL}
    return {"token": tok, "expires_in": TOKEN_TTL, "workout": w, "steps": summary(w), "garmin_json": payload}


def take(token: str) -> dict | None:
    """Single-use: a token can only be sent once (and only before it expires)."""
    with _LOCK:
        p = _PREVIEWS.pop(token or "", None)
    if not p or p["expires"] < time.time() or _digest(p["payload"]) != p["digest"]:
        return None
    return p


def send(api, p: dict) -> dict:
    """upload -> schedule -> (optional) push. Stops at the first failure and reports exactly what happened."""
    w, done = p["workout"], []
    err = lambda step, e: {"status": "partial" if done else "failed", "done": done, "failed_step": step,
                           "error": type(e).__name__, "detail": str(e)[:200]}
    try:
        res = api.upload_workout(p["payload"]) or {}
        wid = res.get("workoutId")
        if not wid:
            raise RuntimeError("Garmin didn't return a workout id")
        done.append({"step": "upload", "workout_id": wid})
    except Exception as e:  # noqa: BLE001
        return err("upload", e)
    try:
        api.schedule_workout(wid, w["date"])
        done.append({"step": "schedule", "date": w["date"]})
    except Exception as e:  # noqa: BLE001
        return {**err("schedule", e), "workout_id": wid}
    if w["push"]:
        try:
            api.push_workout_to_device(wid)
            done.append({"step": "push"})
        except Exception as e:  # noqa: BLE001
            return {**err("push", e), "workout_id": wid}
    return {"status": "ok", "done": done, "workout_id": wid}
