"""
theoretical_minimum.py

Estimates a LOWER BOUND on the soft-penalty score - i.e. the best score any
schedule could possibly achieve given your data - so you know how close the
GA-hybrid's ~2,800 already is to the true floor, and whether further
optimisation is worth chasing.

It reads your ACTUAL data (curriculum_requirements, timeslots, groups, subjects)
and computes, per soft rule, the portion of the penalty that is STRUCTURALLY
UNAVOIDABLE:

  * SUBJECT DISTRIBUTION  (rigorous):  if a class has H weekly hours of a subject
    and there are D teaching days, the subject MUST appear >1x/day on at least
    ceil(H/D)-forcing days. The minimum unavoidable same-day-repeats are exactly
    computable. This is a hard arithmetic floor.

  * BALANCE  (rigorous-ish):  a class has T total weekly hours spread over D days,
    but each day is capped (8 Sun-Thu, 4 Fri). The most balanced possible split
    still has a spread caused by Friday's cap. We compute the minimum possible
    max-day-minus-min-day and its penalty.

  * DISMISSAL  (estimate):  if a grade's hours don't fit within its dismissal
    window (last allowed period x days), some lessons MUST spill past dismissal.
    We estimate the minimum forced spill.

  * HOLY MORNINGS  (estimate):  if a class's holy hours exceed the available
    morning slots (threshold x days, shared with hard subjects), some holy
    lessons MUST be in the afternoon. Rough estimate.

The SUM is a LOWER BOUND: no schedule can score below it (on these rules). The
true optimum is >= this bound. Comparing GA-hybrid's score to this tells you how
much room, if any, is left.

IMPORTANT: this is a BOUND / ESTIMATE, not a target the solver must hit. The real
optimum is usually somewhat ABOVE this bound (the bound ignores interactions
between rules). But it's a useful yardstick: if GA-hybrid is close to the bound,
you're near optimal; if far above, there may be room.

Run from repo root:
    python theoretical_minimum.py
"""

import math
from collections import defaultdict

from data_access import fetch_all_data

# Penalty constants (match scoring_config.py on the server).
SUBJECT_DISTRIBUTION_PENALTY_PER_EXTRA_HOUR = 5
BALANCE_PENALTY_PER_HOUR = 200
BALANCE_TOLERANCE = 2
DISMISSAL_PENALTY_PER_HOUR = 60
HOLY_MORNING_PENALTY = 15
HOLY_MORNING_THRESHOLD = 4
GRADE_DISMISSAL = {1: 6, 2: 6, 3: 6, 4: 8, 5: 8, 6: 8}
DEFAULT_DISMISSAL = 8

GRADE_LETTERS = {"א": 1, "ב": 2, "ג": 3, "ד": 4, "ה": 5, "ו": 6}
HOLY_SUBJECTS = {"תורה", "נביא", "הלכה", "חינוך מתוך אמונה", "פרשת שבוע"}


def grade_of(group_name):
    if not group_name:
        return None
    rest = group_name.replace("כיתה", "").strip()
    return GRADE_LETTERS.get(rest[0]) if rest else None


def main():
    data = fetch_all_data()

    # Day structure from timeslots.
    hours_per_day = defaultdict(set)  # day -> set of hours
    for ts in data["timeslots"]:
        hours_per_day[ts["day_of_week"]].add(ts["hour_of_day"])
    days = sorted(hours_per_day.keys())
    n_days = len(days)
    day_capacity = {d: len(hrs) for d, hrs in hours_per_day.items()}  # e.g. Fri=4
    total_slots_per_day = {d: max(hrs) for d, hrs in hours_per_day.items()}

    group_by_id = {g["id"]: g for g in data["student_groups"]}
    subject_by_id = {s["id"]: s for s in data["subjects"]}
    req_by_id = {r["id"]: r for r in data["curriculum_requirements"]}

    # Per class: subject -> weekly hours, and total weekly hours.
    class_subject_hours = defaultdict(lambda: defaultdict(int))
    class_total_hours = defaultdict(int)
    for r in data["curriculum_requirements"]:
        gid = r["student_group_id"]
        class_subject_hours[gid][r["subject_id"]] += r["weekly_hours"]
        class_total_hours[gid] += r["weekly_hours"]

    subj_dist_floor = 0
    balance_floor = 0
    dismissal_floor = 0
    holy_floor = 0

    print("=" * 70)
    print("THEORETICAL MINIMUM (lower bound) - per class breakdown")
    print("=" * 70)

    for gid in sorted(class_total_hours.keys()):
        gname = group_by_id.get(gid, {}).get("group_name", f"group {gid}")
        grade = grade_of(gname)
        total = class_total_hours[gid]

        # ---- SUBJECT DISTRIBUTION floor (rigorous) ----
        # For each subject with H hours over n_days days, the minimum number of
        # "extra same-day" lessons is sum over days of (lessons_that_day - 1)
        # when optimally spread = max(0, H - n_days) if H <= 2*n_days, but more
        # generally: spreading H into n_days days as evenly as possible, the
        # extra-hours penalty counts (per day) count-1 summed = H - (days used).
        # Min extra = H - n_days if H > n_days else 0  (each day gets >=1 until
        # you must double up). This is the minimal forced same-day repeats.
        class_subj_extra = 0
        for sid, H in class_subject_hours[gid].items():
            if H > n_days:
                class_subj_extra += (H - n_days)
        subj_dist_floor += class_subj_extra * SUBJECT_DISTRIBUTION_PENALTY_PER_EXTRA_HOUR

        # ---- BALANCE floor (rigorous-ish) ----
        # Spread total hours as evenly as possible across days, respecting each
        # day's cap. Minimum possible (max_day - min_day).
        # Greedy even split honoring caps:
        caps = [day_capacity[d] for d in days]
        # distribute 'total' as evenly as possible under caps
        alloc = _even_split_under_caps(total, caps)
        if alloc is not None and len(alloc) > 0:
            spread = max(alloc) - min(alloc)
            if spread > BALANCE_TOLERANCE:
                balance_floor += (spread - BALANCE_TOLERANCE) * BALANCE_PENALTY_PER_HOUR
                spread_note = f"min spread {spread} -> +{(spread-BALANCE_TOLERANCE)*BALANCE_PENALTY_PER_HOUR}"
            else:
                spread_note = f"min spread {spread} (within tolerance, +0)"
        else:
            spread_note = "n/a"

        # ---- DISMISSAL floor (estimate) ----
        # Grade's dismissal window: last allowed period * days (but Fri capped).
        dismissal = GRADE_DISMISSAL.get(grade, DEFAULT_DISMISSAL) if grade else DEFAULT_DISMISSAL
        window = sum(min(dismissal, day_capacity[d]) for d in days)
        forced_spill = max(0, total - window)
        dismissal_floor += forced_spill * DISMISSAL_PENALTY_PER_HOUR

        # ---- HOLY MORNINGS floor (estimate) ----
        holy_hours = sum(H for sid, H in class_subject_hours[gid].items()
                         if subject_by_id.get(sid, {}).get("subject_name") in HOLY_SUBJECTS)
        morning_slots = sum(min(HOLY_MORNING_THRESHOLD, day_capacity[d]) for d in days)
        # holy competes with hard subjects for mornings; as a LOOSE lower bound,
        # only count forced afternoon if holy alone exceeds morning slots.
        forced_holy_afternoon = max(0, holy_hours - morning_slots)
        holy_floor += forced_holy_afternoon * HOLY_MORNING_PENALTY

        print(f"\n{gname} (grade {grade}, {total}h/week):")
        print(f"  subj-distribution: +{class_subj_extra * SUBJECT_DISTRIBUTION_PENALTY_PER_EXTRA_HOUR}"
              f"  ({class_subj_extra} forced same-day repeats)")
        print(f"  balance:           {spread_note}")
        print(f"  dismissal:         window={window}h, spill={forced_spill}h -> +{forced_spill*DISMISSAL_PENALTY_PER_HOUR}")
        print(f"  holy mornings:     holy={holy_hours}h, morning slots={morning_slots}, "
              f"forced afternoon={forced_holy_afternoon} -> +{forced_holy_afternoon*HOLY_MORNING_PENALTY}")

    total_floor = subj_dist_floor + balance_floor + dismissal_floor + holy_floor

    print("\n" + "=" * 70)
    print("LOWER-BOUND TOTALS (no schedule can score below these, per rule)")
    print("=" * 70)
    print(f"  Subject distribution (rigorous): {subj_dist_floor}")
    print(f"  Balance (rigorous-ish):          {balance_floor}")
    print(f"  Dismissal (estimate):            {dismissal_floor}")
    print(f"  Holy mornings (estimate):        {holy_floor}")
    print(f"  " + "-" * 45)
    print(f"  THEORETICAL MINIMUM (lower bound): {total_floor}")
    print("=" * 70)
    print(f"\nInterpretation:")
    print(f"  - No valid schedule can score below ~{total_floor} on these soft rules.")
    print(f"  - GA-hybrid reached ~2,800. Compare:")
    print(f"      If {total_floor} is close to 2,800 -> GA-hybrid is near-optimal, little room left.")
    print(f"      If {total_floor} is far below 2,800 -> there may be room to improve further.")
    print(f"  - The TRUE optimum is somewhat ABOVE this bound (it ignores rule interactions),")
    print(f"    so the real gap is smaller than (2,800 - {total_floor}).")


def _even_split_under_caps(total, caps):
    """
    Distribute `total` units across len(caps) bins as evenly as possible without
    exceeding each bin's cap. Returns the allocation list, or None if total
    exceeds sum(caps) (infeasible).
    """
    if total > sum(caps):
        return None
    n = len(caps)
    alloc = [0] * n
    remaining = total
    # Water-filling: repeatedly add 1 to the smallest non-capped bin.
    # Simple O(total) approach (fine for small numbers).
    while remaining > 0:
        # find bins not at cap, pick the one with the current smallest alloc
        candidates = [i for i in range(n) if alloc[i] < caps[i]]
        if not candidates:
            break
        i = min(candidates, key=lambda k: alloc[k])
        alloc[i] += 1
        remaining -= 1
    return alloc


if __name__ == "__main__":
    main()
