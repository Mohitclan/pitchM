"""
Pitchlight — "am I ready to go?" after a rehearsal.

Plain rules, no AI: did you cover the script, open well, get your key points across, keep to time and stay clear?
The answer is one word (Ready / Almost / Not yet) and at most two things to fix.
"""
from __future__ import annotations

VERDICTS = {"ready": "Ready to go", "almost": "Almost there", "not_yet": "Not yet"}


def readiness_check(
    *,
    sections: list[dict],                  # [{title, covered, total}] from ScriptFollower.section_coverage()
    skipped: list[str],                    # titles of skipped sections
    coverage_pct: int,
    key_points_hit: int,
    key_points_total: int,
    duration_s: float,
    target_minutes: int,
    clutter_per_min: float | None,         # fillers + repeated words per speaking minute
    wpm: float | None,
    roleplay: bool = False,
) -> dict:
    checks: list[dict] = []

    opening = sections[0] if sections else None
    opening_ok = bool(opening and opening["total"] and opening["covered"] / opening["total"] >= 0.5)
    checks.append({"id": "opening", "label": "Strong opening", "ok": opening_ok,
                   "detail": (f"{opening['covered']} of {opening['total']} opening lines" if opening else "no script"),
                   "fix": "Start with your opening lines: thank them, say what you'll cover, check it suits them."})

    cov_ok = coverage_pct >= 80
    checks.append({"id": "coverage", "label": "Covered the script", "ok": cov_ok, "detail": f"{coverage_pct}% of the script",
                   "fix": ("You skipped: " + ", ".join(skipped) + ".") if skipped else "Cover every section, even briefly."})

    if key_points_total:
        kp_ok = key_points_hit / key_points_total >= 0.75
        checks.append({"id": "key_points", "label": "Key points got across", "ok": kp_ok,
                       "detail": f"{key_points_hit} of {key_points_total}",
                       "fix": "Say each key point plainly once, in your own words."})

    if target_minutes and duration_s >= 20:
        mins = duration_s / 60
        len_ok = 0.6 * target_minutes <= mins <= 1.4 * target_minutes
        checks.append({"id": "length", "label": "Right length", "ok": len_ok,
                       "detail": f"{mins:.1f} min (aim {target_minutes})",
                       "fix": "Trim the middle sections." if mins > target_minutes else "Add a proof point or an example."})

    if not roleplay and clutter_per_min is not None:
        clean_ok = clutter_per_min <= 4
        checks.append({"id": "clutter", "label": "Clear delivery", "ok": clean_ok,
                       "detail": f"{clutter_per_min:.1f} fillers/repeats a minute",
                       "fix": "Pause for a breath instead of 'um' and repeating words."})

    if not roleplay and wpm:
        pace_ok = 105 <= wpm <= 175
        checks.append({"id": "pace", "label": "Steady pace", "ok": pace_ok, "detail": f"{round(wpm)} words a minute",
                       "fix": "Slow down; finish each line before the next." if wpm > 175 else "Speak a little faster and keep moving."})

    passed = sum(1 for c in checks if c["ok"])
    total = max(1, len(checks))
    critical_fail = (not opening_ok and bool(opening)) or coverage_pct < 50
    if critical_fail or passed / total < 0.6:
        verdict = "not_yet"
    elif passed == total or passed / total >= 0.8:
        verdict = "ready"
    else:
        verdict = "almost"
    fixes = [c["fix"] for c in checks if not c["ok"]][:2]
    return {"verdict": verdict, "label": VERDICTS[verdict], "passed": passed, "total": len(checks),
            "checks": checks, "fix": fixes}
