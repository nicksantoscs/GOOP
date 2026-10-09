"""Goop workout library. Each template maps 1:1 onto the #workout builder spec (see goop/workout.py):
warm-up, a repeat block of 1-6 'parts' (time in min or distance in m, each with an optional HR zone), recovery, cool-down.
Zones are Garmin HR zones, so the watch uses Nicholas's own zone settings."""

def _t(id, sport, goal, name, purpose, parts, repeats=1, warmup_min=0, recovery_min=0, cooldown_min=0):
    return {"id": id, "sport": sport, "goal": goal, "name": name, "purpose": purpose, "warmup_min": warmup_min,
            "repeats": repeats, "parts": [{"v": v, "u": u, "z": z} for v, u, z in parts],
            "recovery_min": recovery_min, "cooldown_min": cooldown_min}

GOALS = ["easy", "tempo", "threshold", "vo2", "hills", "long"]
GOAL_LABELS = {"easy": "Easy/Base", "tempo": "Tempo", "threshold": "Threshold", "vo2": "VO2 max", "hills": "Hills", "long": "Long"}

TEMPLATES = [
    # ---- Run
    _t("run-easy", "running", "easy", "Easy Run", "Aerobic base at conversational effort.", [(40, "min", 2)]),
    _t("run-recovery", "running", "easy", "Recovery Run", "Gentle shake-out to aid recovery after hard days.", [(30, "min", 1)]),
    _t("run-tempo", "running", "tempo", "Tempo Run", "Sustained comfortably-hard effort to raise aerobic ceiling.", [(20, "min", 3)], warmup_min=10, cooldown_min=10),
    _t("run-progression", "running", "tempo", "Progression Run", "Start easy, finish strong: Z2 → Z3 → Z4.", [(20, "min", 2), (15, "min", 3), (10, "min", 4)], warmup_min=10, cooldown_min=5),
    _t("run-cruise", "running", "threshold", "Cruise Intervals 4×6 min", "Threshold volume in digestible chunks.", [(6, "min", 4)], repeats=4, warmup_min=15, recovery_min=2, cooldown_min=10),
    _t("run-threshold", "running", "threshold", "Threshold 2×15 min", "Long threshold blocks to push lactate clearance.", [(15, "min", 4)], repeats=2, warmup_min=15, recovery_min=3, cooldown_min=10),
    _t("run-vo2", "running", "vo2", "VO2 Max 5×3 min", "Hard intervals near max aerobic power.", [(3, "min", 5)], repeats=5, warmup_min=15, recovery_min=3, cooldown_min=10),
    _t("run-400s", "running", "vo2", "400 m Repeats ×10", "Fast, short repeats for speed and economy.", [(400, "m", 5)], repeats=10, warmup_min=15, recovery_min=1.5, cooldown_min=10),
    _t("run-hills", "running", "hills", "Hill Repeats 8×60 s", "Strong uphill efforts, jog back down to recover.", [(1, "min", 5)], repeats=8, warmup_min=15, recovery_min=2, cooldown_min=10),
    _t("run-long", "running", "long", "Long Run", "Endurance and fat adaptation at an easy, steady effort.", [(90, "min", 2)]),
    # ---- Ride
    _t("ride-endurance", "cycling", "easy", "Endurance Ride", "Steady Z2 riding to build the aerobic engine.", [(90, "min", 2)]),
    _t("ride-sweetspot", "cycling", "threshold", "Sweet Spot 3×10 min", "Just under threshold: big fitness return for the fatigue.", [(10, "min", 3)], repeats=3, warmup_min=15, recovery_min=5, cooldown_min=10),
    _t("ride-overunder", "cycling", "threshold", "Over-Unders 3×9 min", "Alternate just under and over threshold to handle surges.", [(2, "min", 4), (1, "min", 5)] * 3, repeats=3, warmup_min=15, recovery_min=5, cooldown_min=10),
    _t("ride-vo2", "cycling", "vo2", "VO2 Max 5×4 min", "Hard 4-minute efforts to lift max aerobic power.", [(4, "min", 5)], repeats=5, warmup_min=15, recovery_min=4, cooldown_min=10),
    _t("ride-hills", "cycling", "hills", "Climbing Repeats 6×5 min", "Seated climbing strength at threshold effort.", [(5, "min", 4)], repeats=6, warmup_min=15, recovery_min=3, cooldown_min=10),
    _t("ride-long", "cycling", "long", "Long Ride", "Long Z2 endurance for durability.", [(180, "min", 2)]),
]
