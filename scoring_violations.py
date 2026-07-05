"""
scoring_violations.py

Companion to genetic/solver.py's _score_schedule. Computes the SAME total
penalty, but also returns a human-readable list of exactly what was
penalised and by how much - so the admin can see WHY a score is what it is.

It reads a Genetic-style schedule: {assignment_id: [timeslot_id, ...]}.

Returns: (total_penalty, violations)
  violations = [ {"type": str, "detail": str (Hebrew), "penalty": number,
                  "severity": "hard" | "soft"}, ... ]

Kept in its own module so the fast per-generation scorer in the GA loop is
untouched; this heavier version runs ONCE, on the final best schedule.
"""

from scoring_config import (
    HARD_CONSTRAINT_PENALTY,
    TEACHER_HARD_CONSTRAINT_PENALTY,
    SOFT_CONSTRAINT_WEIGHT_MULTIPLIER,
    OUTSIDE_HOURS_RANGE_PENALTY_PER_HOUR,
    SUBJECT_DISTRIBUTION_PENALTY_PER_EXTRA_HOUR,
    PREFERENCE_WEIGHTS,
)

DAY_NAMES = {1: "ראשון", 2: "שני", 3: "שלישי", 4: "רביעי", 5: "חמישי", 6: "שישי"}


def _day_hour(timeslot_by_id, t):
    ts = timeslot_by_id.get(t)
    if ts is None:
        return f"משבצת {t}"
    return f"{DAY_NAMES.get(ts['day_of_week'], ts['day_of_week'])} שעה {ts['hour_of_day']}"


def score_genetic_schedule_with_violations(schedule, data, lookups):
    total_penalty = 0.0
    violations = []

    teacher_assignments = data["teacher_assignments"]
    requirement_by_id = lookups["requirement_by_id"]
    constraint_by_teacher_timeslot = lookups["constraint_by_teacher_timeslot"]
    preferences_by_teacher = lookups["preferences_by_teacher"]
    timeslot_by_id = lookups["timeslot_by_id"]
    subject_by_id = lookups["subject_by_id"]
    group_by_id = lookups["group_by_id"]
    room_count_by_type = lookups["room_count_by_type"]
    assignments_by_teacher = lookups["assignments_by_teacher"]

    teacher_by_id = {t["id"]: t for t in data["teachers"]}

    def teacher_name(tid):
        t = teacher_by_id.get(tid)
        return f"{t['first_name']} {t['last_name']}" if t else f"מורה {tid}"

    def add(total_add, vtype, detail, severity):
        return total_add, {"type": vtype, "detail": detail, "penalty": total_add, "severity": severity}

    # ---- Structural: teacher double-booking ----
    timeslot_count_by_teacher = {}
    for ta in teacher_assignments:
        counts = timeslot_count_by_teacher.setdefault(ta["teacher_id"], {})
        for t in schedule[ta["id"]]:
            counts[t] = counts.get(t, 0) + 1
    for teacher_id, counts in timeslot_count_by_teacher.items():
        for t, count in counts.items():
            if count > 1:
                pen = (count - 1) * HARD_CONSTRAINT_PENALTY
                total_penalty += pen
                violations.append({
                    "type": "teacher_double_booked",
                    "detail": f"{teacher_name(teacher_id)}: {count} שיעורים באותה משבצת ({_day_hour(timeslot_by_id, t)})",
                    "penalty": pen, "severity": "hard",
                })

    # ---- Structural: student group double-booking ----
    timeslot_count_by_group = {}
    for ta in teacher_assignments:
        req = requirement_by_id[ta["cur_requirement_id"]]
        counts = timeslot_count_by_group.setdefault(req["student_group_id"], {})
        for t in schedule[ta["id"]]:
            counts[t] = counts.get(t, 0) + 1
    for group_id, counts in timeslot_count_by_group.items():
        for t, count in counts.items():
            if count > 1:
                pen = (count - 1) * HARD_CONSTRAINT_PENALTY
                total_penalty += pen
                gname = group_by_id.get(group_id, {}).get("group_name", f"קבוצה {group_id}")
                violations.append({
                    "type": "group_double_booked",
                    "detail": f"{gname}: {count} שיעורים באותה משבצת ({_day_hour(timeslot_by_id, t)})",
                    "penalty": pen, "severity": "hard",
                })

    # ---- Structural: weekly_hours correctness ----
    for ta in teacher_assignments:
        req = requirement_by_id[ta["cur_requirement_id"]]
        expected = req["weekly_hours"]
        actual = len(schedule[ta["id"]])
        if actual != expected:
            pen = abs(expected - actual) * HARD_CONSTRAINT_PENALTY
            total_penalty += pen
            subj = subject_by_id.get(req["subject_id"], {}).get("subject_name", "מקצוע")
            gname = group_by_id.get(req["student_group_id"], {}).get("group_name", "קבוצה")
            violations.append({
                "type": "weekly_hours",
                "detail": f"{teacher_name(ta['teacher_id'])} / {subj} / {gname}: שובצו {actual} מתוך {expected} שעות נדרשות",
                "penalty": pen, "severity": "hard",
            })

    # ---- Room-awareness: simultaneous room-constrained lessons ----
    timeslot_room_demand = {}
    for ta in teacher_assignments:
        req = requirement_by_id[ta["cur_requirement_id"]]
        subject = subject_by_id[req["subject_id"]]
        if subject["required_room_id"] is not None:
            resource = ("specific", subject["required_room_id"])
        elif subject.get("required_room_type") is not None:
            resource = ("type", subject["required_room_type"])
        else:
            continue
        for t in schedule[ta["id"]]:
            demands = timeslot_room_demand.setdefault(t, {})
            demands[resource] = demands.get(resource, 0) + 1
    for t, demands in timeslot_room_demand.items():
        for resource, count in demands.items():
            max_capacity = 1 if resource[0] == "specific" else room_count_by_type.get(resource[1], 0)
            if count > max_capacity:
                pen = (count - max_capacity) * HARD_CONSTRAINT_PENALTY
                total_penalty += pen
                res_desc = "חדר ייעודי" if resource[0] == "specific" else f"חדר מסוג '{resource[1]}'"
                violations.append({
                    "type": "room_capacity",
                    "detail": f"{res_desc}: {count} שיעורים דורשים אותו משאב ({_day_hour(timeslot_by_id, t)}), זמינים {max_capacity}",
                    "penalty": pen, "severity": "hard",
                })

    # ---- Structural: sync_block_identity ----
    sync_blocks = {}
    for ta in teacher_assignments:
        req = requirement_by_id[ta["cur_requirement_id"]]
        sbi = req.get("sync_block_identity")
        if sbi is not None:
            sync_blocks.setdefault(sbi, []).append(ta["id"])
    for sbi, assignment_ids in sync_blocks.items():
        if len(assignment_ids) < 2:
            continue
        reference_slots = sorted(schedule[assignment_ids[0]])
        for a_id in assignment_ids[1:]:
            other_slots = sorted(schedule[a_id])
            if reference_slots != other_slots:
                diff_count = len(set(reference_slots).symmetric_difference(set(other_slots)))
                pen = diff_count * HARD_CONSTRAINT_PENALTY
                total_penalty += pen
                violations.append({
                    "type": "sync_block",
                    "detail": f"בלוק מסונכרן {sbi}: שיעורים שאמורים להתקיים יחד שובצו במשבצות שונות",
                    "penalty": pen, "severity": "hard",
                })

    # ---- Data-driven: teacher_constraints (hard/soft) ----
    for ta in teacher_assignments:
        teacher_id = ta["teacher_id"]
        for t in schedule[ta["id"]]:
            constraint = constraint_by_teacher_timeslot.get((teacher_id, t))
            if constraint is not None:
                if constraint["constraint_type"] == "hard":
                    total_penalty += TEACHER_HARD_CONSTRAINT_PENALTY
                    violations.append({
                        "type": "teacher_cannot",
                        "detail": f"{teacher_name(teacher_id)}: שובץ בזמן שסומן 'לא יכול' ({_day_hour(timeslot_by_id, t)})",
                        "penalty": TEACHER_HARD_CONSTRAINT_PENALTY, "severity": "hard",
                    })
                else:
                    pen = constraint["weight"] * SOFT_CONSTRAINT_WEIGHT_MULTIPLIER
                    total_penalty += pen
                    violations.append({
                        "type": "teacher_prefers_not",
                        "detail": f"{teacher_name(teacher_id)}: שובץ בזמן שסומן 'מעדיף שלא' ({_day_hour(timeslot_by_id, t)})",
                        "penalty": pen, "severity": "soft",
                    })

    # ---- teacher_preferences (soft) ----
    for teacher_id, assignment_ids in assignments_by_teacher.items():
        prefs = preferences_by_teacher.get(teacher_id)
        if prefs is None:
            continue
        total_hours = sum(len(schedule[a_id]) for a_id in assignment_ids)
        if prefs["min_hours"] is not None and total_hours < prefs["min_hours"]:
            pen = (prefs["min_hours"] - total_hours) * OUTSIDE_HOURS_RANGE_PENALTY_PER_HOUR
            total_penalty += pen
            violations.append({"type": "hours_range", "detail": f"{teacher_name(teacher_id)}: {total_hours} שעות, מתחת למינימום ({prefs['min_hours']})", "penalty": pen, "severity": "soft"})
        if prefs["max_hours"] is not None and total_hours > prefs["max_hours"]:
            pen = (total_hours - prefs["max_hours"]) * OUTSIDE_HOURS_RANGE_PENALTY_PER_HOUR
            total_penalty += pen
            violations.append({"type": "hours_range", "detail": f"{teacher_name(teacher_id)}: {total_hours} שעות, מעל המקסימום ({prefs['max_hours']})", "penalty": pen, "severity": "soft"})

        teacher_day_hours = {}
        for a_id in assignment_ids:
            for t in schedule[a_id]:
                ts = timeslot_by_id[t]
                teacher_day_hours.setdefault(ts["day_of_week"], []).append(ts["hour_of_day"])

        teaching_days = len(teacher_day_hours)
        if (6 - teaching_days) == 0 and prefs.get("priority_free_day") and prefs["priority_free_day"] > 0:
            pen = PREFERENCE_WEIGHTS["free_day"] * prefs["priority_free_day"]
            total_penalty += pen
            violations.append({"type": "free_day", "detail": f"{teacher_name(teacher_id)}: אין יום חופשי", "penalty": pen, "severity": "soft"})

        for day, hours in teacher_day_hours.items():
            hs = sorted(hours)
            gaps = (hs[-1] - hs[0] + 1) - len(hs)
            if gaps > 0 and prefs.get("priority_no_gaps") and prefs["priority_no_gaps"] > 0:
                pen = gaps * PREFERENCE_WEIGHTS["no_gaps"] * prefs["priority_no_gaps"]
                total_penalty += pen
                violations.append({"type": "teacher_gaps", "detail": f"{teacher_name(teacher_id)}: {gaps} חלונות ביום {DAY_NAMES.get(day, day)}", "penalty": pen, "severity": "soft"})
            if hs[-1] > 6 and prefs.get("priority_early_finish") and prefs["priority_early_finish"] > 0:
                pen = (hs[-1] - 6) * PREFERENCE_WEIGHTS["early_finish"] * prefs["priority_early_finish"]
                total_penalty += pen
                violations.append({"type": "early_finish", "detail": f"{teacher_name(teacher_id)}: מסיים בשעה {hs[-1]} ביום {DAY_NAMES.get(day, day)}", "penalty": pen, "severity": "soft"})
            if gaps > 0 and prefs.get("priority_consecutive") and prefs["priority_consecutive"] > 0:
                pen = gaps * PREFERENCE_WEIGHTS["consecutive"] * prefs["priority_consecutive"]
                total_penalty += pen
                violations.append({"type": "consecutive", "detail": f"{teacher_name(teacher_id)}: שיעורים לא רצופים ביום {DAY_NAMES.get(day, day)}", "penalty": pen, "severity": "soft"})

    # ---- Subject distribution ----
    group_subject_day_counts = {}
    for ta in teacher_assignments:
        req = requirement_by_id[ta["cur_requirement_id"]]
        for t in schedule[ta["id"]]:
            ts = timeslot_by_id[t]
            key = (req["student_group_id"], req["subject_id"], ts["day_of_week"])
            group_subject_day_counts[key] = group_subject_day_counts.get(key, 0) + 1
    for (group_id, subject_id, day), count in group_subject_day_counts.items():
        if count > 1:
            pen = (count - 1) * SUBJECT_DISTRIBUTION_PENALTY_PER_EXTRA_HOUR
            total_penalty += pen
            gname = group_by_id.get(group_id, {}).get("group_name", "קבוצה")
            subj = subject_by_id.get(subject_id, {}).get("subject_name", "מקצוע")
            violations.append({"type": "subject_distribution", "detail": f"{gname} / {subj}: {count} שיעורים באותו יום ({DAY_NAMES.get(day, day)})", "penalty": pen, "severity": "soft"})

    violations.sort(key=lambda v: v["penalty"], reverse=True)
    return total_penalty, violations
