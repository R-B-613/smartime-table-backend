"""
hill_climbing/solver.py

Hill Climbing solver for the timetabling problem, with Random Restarts to
escape local optima.

This module does NOT touch the database directly - it receives plain
Python data (as fetched by data_access.fetch_all_data()) and returns a
plain Python result dict, in the EXACT SAME SHAPE as csp.solver.run_csp(),
so it can be measured/saved/compared identically.

-------------------------------------------------------------------------
KEY DIFFERENCE FROM CSP (read this first)
-------------------------------------------------------------------------
CSP used OR-Tools to make certain rules STRUCTURALLY IMPOSSIBLE to break
(a teacher can never be double-booked, because the solver's model forbids
it outright). Hill Climbing has no such mechanism - it only understands
"better score" or "worse score". This means EVERY rule that CSP got "for
free" must be turned into a scored penalty here instead, including:
    - teacher double-booking
    - student group double-booking
    - each requirement getting exactly its weekly_hours

These are scored using the exact same HARD_CONSTRAINT_PENALTY as the
"hard" rows of teacher_constraints, since conceptually they're the same
kind of rule (a structural impossibility) - it's just that CSP enforces
them for free while Hill Climbing has to "pay" to discover them via
scoring, the same way it discovers everything else.

-------------------------------------------------------------------------
SCHEDULE REPRESENTATION
-------------------------------------------------------------------------
A candidate schedule is a plain Python dict:

    schedule = {
        assignment_id: [timeslot_id, timeslot_id, ...],
        ...
    }

Each teacher_assignment maps to a LIST of timeslot ids - one per weekly
hour it needs (e.g. a 6-hour/week assignment has a list of 6 timeslot
ids, possibly with repeats in the list if generated carelessly - the
scoring function is what penalizes an assignment occupying the same
timeslot twice, since that would itself be a kind of conflict it should
generally avoid for sane usage of timeslots, though the literal scoring
config does not currently add a specific penalty for within-assignment
repeats - see TODO below).

TODO (explicitly deferred for v1, same as CSP):
    - Room capacity / room-type matching (room assignment, like CSP,
      is decided AFTER the timeslot schedule is finalized)
    - sync_block_identity handling
    - Full preference scoring (gaps/free-day/consecutive/early-finish) -
      only the min/max hours-range preference check is implemented,
      mirroring CSP's current state.
-------------------------------------------------------------------------
"""

import random
import time

from scoring_config import (
    HARD_CONSTRAINT_PENALTY,
    SOFT_CONSTRAINT_WEIGHT_MULTIPLIER,
    OUTSIDE_HOURS_RANGE_PENALTY_PER_HOUR,
    SUBJECT_DISTRIBUTION_PENALTY_PER_EXTRA_HOUR,
    PREFERENCE_WEIGHTS,
    ALGO_HILL_CLIMBING,
)

# How long (seconds) the WHOLE algorithm is allowed to run, across all
# restarts combined. Matches CSP's max_time_in_seconds for a fair
# comparison - tune this independently later via trial and error.
HILL_CLIMBING_TIME_BUDGET_SECONDS = 60.0

# Safety cap on neighbors sampled per climbing step (instead of checking
# EVERY possible neighbor, which would be assignment_count * timeslot_count
# - potentially thousands - we sample a subset each step for speed).
NEIGHBORS_SAMPLED_PER_STEP = 40


# ---------------------------------------------------------------------------
# Lookup maps (mirrors csp/solver.py's _build_lookup_maps, same idea)
# ---------------------------------------------------------------------------

def _build_lookup_maps(data: dict):
    curriculum_requirements = data["curriculum_requirements"]
    teacher_assignments = data["teacher_assignments"]
    teacher_constraints = data["teacher_constraints"]
    teacher_preferences = data["teacher_preferences"]
    timeslots = data["timeslots"]
    subjects = data["subjects"]
    student_groups = data["student_groups"]
    rooms = data["rooms"]

    requirement_by_id = {req["id"]: req for req in curriculum_requirements}
    assignment_by_id = {ta["id"]: ta for ta in teacher_assignments}

    assignments_by_teacher = {}
    for ta in teacher_assignments:
        assignments_by_teacher.setdefault(ta["teacher_id"], []).append(ta["id"])

    assignments_by_group = {}
    for ta in teacher_assignments:
        req = requirement_by_id[ta["cur_requirement_id"]]
        assignments_by_group.setdefault(req["student_group_id"], []).append(ta["id"])

    constraint_by_teacher_timeslot = {
        (c["teacher_id"], c["timeslot_id"]): c for c in teacher_constraints
    }

    preferences_by_teacher = {p["teacher_id"]: p for p in teacher_preferences}

    timeslot_by_id = {ts["id"]: ts for ts in timeslots}
    subject_by_id = {s["id"]: s for s in subjects}
    group_by_id = {g["id"]: g for g in student_groups}

    room_count_by_type = {}
    for room in rooms:
        rt = room.get("room_type")
        if rt is not None:
            room_count_by_type[rt] = room_count_by_type.get(rt, 0) + 1

    return {
        "requirement_by_id": requirement_by_id,
        "assignment_by_id": assignment_by_id,
        "assignments_by_teacher": assignments_by_teacher,
        "assignments_by_group": assignments_by_group,
        "constraint_by_teacher_timeslot": constraint_by_teacher_timeslot,
        "preferences_by_teacher": preferences_by_teacher,
        "timeslot_by_id": timeslot_by_id,
        "subject_by_id": subject_by_id,
        "group_by_id": group_by_id,
        "room_count_by_type": room_count_by_type,
    }

# ---------------------------------------------------------------------------
# Initial schedule generation (conflict-aware random)
# ---------------------------------------------------------------------------

def _generate_initial_schedule(data, lookups, timeslot_ids):
    """
    Builds a starting schedule, trying to avoid the most obvious conflicts
    (teacher double-booking, group double-booking) WHEN a non-conflicting
    slot is conveniently available - but does not guarantee a perfectly
    legal schedule. This gives Hill Climbing a reasonable starting point
    without the cost of building a fully correct solver from scratch
    (that's what CSP is for).

    Returns a schedule dict: {assignment_id: [timeslot_id, ...]}
    """
    teacher_assignments = data["teacher_assignments"]
    requirement_by_id = lookups["requirement_by_id"]

    schedule = {}

    # Track, as we go, which timeslots are already used by each teacher
    # and each student group - so we can TRY to avoid obvious conflicts.
    used_timeslots_by_teacher = {}
    used_timeslots_by_group = {}

    # Process assignments in random order each time, so different calls
    # (different restarts) explore different starting points.
    shuffled_assignments = teacher_assignments[:]
    random.shuffle(shuffled_assignments)

    for ta in shuffled_assignments:
        req = requirement_by_id[ta["cur_requirement_id"]]
        weekly_hours = req["weekly_hours"]
        teacher_id = ta["teacher_id"]
        group_id = req["student_group_id"]

        teacher_used = used_timeslots_by_teacher.setdefault(teacher_id, set())
        group_used = used_timeslots_by_group.setdefault(group_id, set())

        chosen_timeslots = []
        for _ in range(weekly_hours):
            # Try a handful of random candidates, prefer one that doesn't
            # conflict with this teacher OR this group yet.
            candidate = None
            for _attempt in range(10):
                t = random.choice(timeslot_ids)
                if t not in teacher_used and t not in group_used:
                    candidate = t
                    break
            if candidate is None:
                # Gave up avoiding conflict after 10 tries - just place it
                # anywhere. Hill Climbing's scoring will catch/penalize
                # this if it's actually a conflict.
                candidate = random.choice(timeslot_ids)

            chosen_timeslots.append(candidate)
            teacher_used.add(candidate)
            group_used.add(candidate)

        schedule[ta["id"]] = chosen_timeslots

    return schedule


# ---------------------------------------------------------------------------
# Scoring (mirrors csp/solver.py's _compute_penalty_score, PLUS the
# structural checks that CSP got for free but we must score here)
# ---------------------------------------------------------------------------

def _score_schedule(schedule, data, lookups):
    """
    Computes the FULL unified penalty score for a candidate schedule.
    Lower is better, 0 = perfect. Covers:
      - Structural: teacher/group double-booking, weekly_hours correctness
      - Room-awareness: simultaneous lessons exceeding available rooms
      - sync_block_identity: synced lessons must share identical timeslots
      - Data-driven: teacher_constraints (hard/soft)
      - Preferences: hours range, gaps, free day, early finish, consecutive
      - Subject distribution: same subject same day same group penalty
    """
    total_penalty = 0.0

    teacher_assignments = data["teacher_assignments"]
    requirement_by_id = lookups["requirement_by_id"]
    constraint_by_teacher_timeslot = lookups["constraint_by_teacher_timeslot"]
    preferences_by_teacher = lookups["preferences_by_teacher"]
    timeslot_by_id = lookups["timeslot_by_id"]
    subject_by_id = lookups["subject_by_id"]
    room_count_by_type = lookups["room_count_by_type"]

    # ---- Structural: teacher double-booking ----
    timeslot_count_by_teacher = {}
    for ta in teacher_assignments:
        teacher_id = ta["teacher_id"]
        counts = timeslot_count_by_teacher.setdefault(teacher_id, {})
        for t in schedule[ta["id"]]:
            counts[t] = counts.get(t, 0) + 1

    for teacher_id, counts in timeslot_count_by_teacher.items():
        for t, count in counts.items():
            if count > 1:
                total_penalty += (count - 1) * HARD_CONSTRAINT_PENALTY

    # ---- Structural: student group double-booking ----
    timeslot_count_by_group = {}
    for ta in teacher_assignments:
        req = requirement_by_id[ta["cur_requirement_id"]]
        group_id = req["student_group_id"]
        counts = timeslot_count_by_group.setdefault(group_id, {})
        for t in schedule[ta["id"]]:
            counts[t] = counts.get(t, 0) + 1

    for group_id, counts in timeslot_count_by_group.items():
        for t, count in counts.items():
            if count > 1:
                total_penalty += (count - 1) * HARD_CONSTRAINT_PENALTY

    # ---- Structural: weekly_hours correctness ----
    for ta in teacher_assignments:
        req = requirement_by_id[ta["cur_requirement_id"]]
        expected_hours = req["weekly_hours"]
        actual_hours = len(schedule[ta["id"]])
        if actual_hours != expected_hours:
            total_penalty += abs(expected_hours - actual_hours) * HARD_CONSTRAINT_PENALTY

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
            if resource[0] == "specific":
                max_capacity = 1
            else:
                max_capacity = room_count_by_type.get(resource[1], 0)
            if count > max_capacity:
                total_penalty += (count - max_capacity) * HARD_CONSTRAINT_PENALTY

    # ---- Structural: sync_block_identity (synced lessons same timeslots) ----
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
                ref_set = set(reference_slots)
                other_set = set(other_slots)
                diff_count = len(ref_set.symmetric_difference(other_set))
                total_penalty += diff_count * HARD_CONSTRAINT_PENALTY

    # ---- Data-driven: teacher_constraints (hard/soft) ----
    for ta in teacher_assignments:
        teacher_id = ta["teacher_id"]
        for t in schedule[ta["id"]]:
            constraint = constraint_by_teacher_timeslot.get((teacher_id, t))
            if constraint is not None:
                if constraint["constraint_type"] == "hard":
                    total_penalty += HARD_CONSTRAINT_PENALTY
                else:
                    total_penalty += constraint["weight"] * SOFT_CONSTRAINT_WEIGHT_MULTIPLIER

    # ---- Data-driven: teacher_preferences (full implementation) ----
    assignments_by_teacher = lookups["assignments_by_teacher"]
    for teacher_id, assignment_ids in assignments_by_teacher.items():
        prefs = preferences_by_teacher.get(teacher_id)
        if prefs is None:
            continue

        total_hours = sum(len(schedule[a_id]) for a_id in assignment_ids)

        # Hours range
        if prefs["min_hours"] is not None and total_hours < prefs["min_hours"]:
            total_penalty += (prefs["min_hours"] - total_hours) * OUTSIDE_HOURS_RANGE_PENALTY_PER_HOUR
        if prefs["max_hours"] is not None and total_hours > prefs["max_hours"]:
            total_penalty += (total_hours - prefs["max_hours"]) * OUTSIDE_HOURS_RANGE_PENALTY_PER_HOUR

        # Collect (day, hour) pairs for this teacher
        teacher_day_hours = {}
        for a_id in assignment_ids:
            for t in schedule[a_id]:
                ts = timeslot_by_id[t]
                teacher_day_hours.setdefault(ts["day_of_week"], []).append(ts["hour_of_day"])

        teaching_days = len(teacher_day_hours)

        # Free day preference (penalize if teacher has zero free days)
        free_days = 6 - teaching_days
        if free_days == 0 and prefs.get("priority_free_day") and prefs["priority_free_day"] > 0:
            total_penalty += PREFERENCE_WEIGHTS["free_day"] * prefs["priority_free_day"]

        # Per-day preferences: gaps, early finish, consecutive
        for day, hours in teacher_day_hours.items():
            hours_sorted = sorted(hours)
            first_hour = hours_sorted[0]
            last_hour = hours_sorted[-1]
            expected_if_no_gaps = last_hour - first_hour + 1
            actual_count = len(hours_sorted)
            gaps = expected_if_no_gaps - actual_count

            # No gaps
            if gaps > 0 and prefs.get("priority_no_gaps") and prefs["priority_no_gaps"] > 0:
                total_penalty += gaps * PREFERENCE_WEIGHTS["no_gaps"] * prefs["priority_no_gaps"]

            # Early finish (penalize teaching past hour 6)
            if last_hour > 6 and prefs.get("priority_early_finish") and prefs["priority_early_finish"] > 0:
                total_penalty += (last_hour - 6) * PREFERENCE_WEIGHTS["early_finish"] * prefs["priority_early_finish"]

            # Consecutive (also penalizes gaps, from a different preference angle)
            if gaps > 0 and prefs.get("priority_consecutive") and prefs["priority_consecutive"] > 0:
                total_penalty += gaps * PREFERENCE_WEIGHTS["consecutive"] * prefs["priority_consecutive"]

    # ---- Subject distribution across the week ----
    group_subject_day_counts = {}
    for ta in teacher_assignments:
        req = requirement_by_id[ta["cur_requirement_id"]]
        group_id = req["student_group_id"]
        subject_id = req["subject_id"]
        for t in schedule[ta["id"]]:
            ts = timeslot_by_id[t]
            key = (group_id, subject_id, ts["day_of_week"])
            group_subject_day_counts[key] = group_subject_day_counts.get(key, 0) + 1

    for key, count in group_subject_day_counts.items():
        if count > 1:
            total_penalty += (count - 1) * SUBJECT_DISTRIBUTION_PENALTY_PER_EXTRA_HOUR

    return total_penalty


def _find_conflicting_assignment_ids(schedule, data, lookups):
    """
    Returns the SET of assignment_ids that are currently involved in at
    least one hard conflict (teacher double-booked OR group double-booked
    at some shared timeslot). Used to bias neighbor selection toward
    fixing actual problems, instead of picking moves completely at random.

    This does NOT include soft-constraint or preference violations on
    purpose - those are comparatively minor (small weights), and chasing
    them with the same urgency as hard conflicts would dilute the repair
    focus. Hard conflicts (10,000 each) are overwhelmingly the most
    valuable thing to fix first.
    """
    teacher_assignments = data["teacher_assignments"]
    requirement_by_id = lookups["requirement_by_id"]

    conflicting_ids = set()

    # Teacher double-booking: for each teacher, find timeslots used more
    # than once, then mark EVERY assignment of that teacher that uses that
    # timeslot as conflicting (we don't know which specific occurrence is
    # "the problem" - any of them moving could resolve it).
    assignments_by_teacher = {}
    for ta in teacher_assignments:
        assignments_by_teacher.setdefault(ta["teacher_id"], []).append(ta)

    for teacher_id, t_assignments in assignments_by_teacher.items():
        timeslot_to_assignment_ids = {}
        for ta in t_assignments:
            for t in schedule[ta["id"]]:
                timeslot_to_assignment_ids.setdefault(t, []).append(ta["id"])

        for t, assignment_ids in timeslot_to_assignment_ids.items():
            if len(assignment_ids) > 1:
                conflicting_ids.update(assignment_ids)

    # Student group double-booking: same idea, grouped by student_group_id.
    assignments_by_group = {}
    for ta in teacher_assignments:
        req = requirement_by_id[ta["cur_requirement_id"]]
        assignments_by_group.setdefault(req["student_group_id"], []).append(ta)

    for group_id, g_assignments in assignments_by_group.items():
        timeslot_to_assignment_ids = {}
        for ta in g_assignments:
            for t in schedule[ta["id"]]:
                timeslot_to_assignment_ids.setdefault(t, []).append(ta["id"])

        for t, assignment_ids in timeslot_to_assignment_ids.items():
            if len(assignment_ids) > 1:
                conflicting_ids.update(assignment_ids)

    return conflicting_ids


# ---------------------------------------------------------------------------
# Neighbor generation + single climb (one hill, until no improvement found)
# ---------------------------------------------------------------------------

def _get_random_neighbor(schedule, timeslot_ids, preferred_assignment_ids=None):
    """
    Builds ONE neighbor schedule: a copy of `schedule` with exactly one
    scheduled session moved to a different (random) timeslot.

    If `preferred_assignment_ids` is given and non-empty, the assignment
    to move is chosen from THAT set instead of from all assignments -
    this is what lets the climb target known conflicts directly, instead
    of searching blindly across the whole schedule.

    Returns a brand new schedule dict (does not mutate the input).
    """
    if preferred_assignment_ids:
        assignment_id = random.choice(list(preferred_assignment_ids))
    else:
        assignment_id = random.choice(list(schedule.keys()))

    hour_index = random.randrange(len(schedule[assignment_id]))

    new_schedule = {a_id: list(timeslots) for a_id, timeslots in schedule.items()}
    new_timeslot = random.choice(timeslot_ids)
    new_schedule[assignment_id][hour_index] = new_timeslot

    return new_schedule


def _climb(initial_schedule, data, lookups, timeslot_ids, deadline):
    """
    Runs ONE hill-climb from initial_schedule until no sampled neighbor
    improves the score (a local optimum), or the overall time deadline
    is reached.

    Neighbor sampling is conflict-targeted: each step, we first check
    which assignments are currently involved in a hard conflict
    (teacher/group double-booking). If any exist, MOST sampled neighbors
    move one of those specifically (targeted repair), while a smaller
    portion still sample fully at random (so soft-constraint/preference
    improvements aren't ignored once hard conflicts are gone, and so we
    don't get stuck only ever looking at the same few assignments).

    Returns (best_schedule, best_score) found during this climb.
    """
    current_schedule = initial_schedule
    current_score = _score_schedule(current_schedule, data, lookups)

    while time.perf_counter() < deadline:
        conflicting_ids = _find_conflicting_assignment_ids(current_schedule, data, lookups)

        best_neighbor = None
        best_neighbor_score = current_score  # only accept STRICT improvements

        for i in range(NEIGHBORS_SAMPLED_PER_STEP):
            # 80% of samples target known conflicts (if any exist), the
            # rest sample fully at random - keeps repair focused without
            # losing the ability to improve soft constraints once the
            # schedule is structurally clean.
            use_targeted = conflicting_ids and (i % 5 != 0)
            neighbor = _get_random_neighbor(
                current_schedule,
                timeslot_ids,
                preferred_assignment_ids=conflicting_ids if use_targeted else None,
            )
            neighbor_score = _score_schedule(neighbor, data, lookups)
            if neighbor_score < best_neighbor_score:
                best_neighbor = neighbor
                best_neighbor_score = neighbor_score

        if best_neighbor is None:
            # No sampled neighbor improved on the current schedule -
            # local optimum reached for this climb.
            break

        current_schedule = best_neighbor
        current_score = best_neighbor_score

        if current_score == 0:
            # Perfect schedule found - no point climbing further.
            break

    return current_schedule, current_score


# ---------------------------------------------------------------------------
# Room assignment (identical simple v1 approach to CSP, applied AFTER the
# timeslot schedule is finalized)
# ---------------------------------------------------------------------------

def _assign_rooms(schedule, data, timeslot_ids):
    """
    Smart room assignment, applied AFTER the timeslot schedule is finalized.
    Three-tier priority per scheduled session:
      1. Subject has a specific required room (required_room_id) -> use it
      2. Subject needs a room TYPE (required_room_type) -> any free room
         of that type
      3. Default -> group's home_room_id, falling back to any free room
         with sufficient capacity
    All tiers check: room is free at this timeslot AND capacity >= group size.
    """
    rooms = data["rooms"]
    subjects = data["subjects"]
    student_groups = data["student_groups"]
    teacher_assignments = data["teacher_assignments"]
    curriculum_requirements = data["curriculum_requirements"]

    room_by_id = {r["id"]: r for r in rooms}
    subject_by_id = {s["id"]: s for s in subjects}
    group_by_id = {g["id"]: g for g in student_groups}
    requirement_by_id = {req["id"]: req for req in curriculum_requirements}
    assignment_by_id = {ta["id"]: ta for ta in teacher_assignments}

    schedule_entries = []
    used_rooms_by_timeslot = {t: set() for t in timeslot_ids}

    for assignment_id, timeslots in schedule.items():
        ta = assignment_by_id[assignment_id]
        req = requirement_by_id[ta["cur_requirement_id"]]
        subject = subject_by_id[req["subject_id"]]
        group = group_by_id[req["student_group_id"]]

        for t in timeslots:
            assigned_room = None

            if subject["required_room_id"] is not None:
                # Priority 1: subject needs ONE specific dedicated room.
                room_id = subject["required_room_id"]
                room = room_by_id.get(room_id)
                if (room
                        and room_id not in used_rooms_by_timeslot[t]
                        and room["capacity"] >= group["student_count"]):
                    assigned_room = room_id
                # If busy or too small: stays None (a real conflict).

            elif subject.get("required_room_type") is not None:
                # Priority 2: subject needs any room of a certain type.
                for room in rooms:
                    if (room.get("room_type") == subject["required_room_type"]
                            and room["id"] not in used_rooms_by_timeslot[t]
                            and room["capacity"] >= group["student_count"]):
                        assigned_room = room["id"]
                        break
                # If all rooms of this type are busy: stays None.

            else:
                # Priority 3: use the group's home (parent) classroom.
                home_room_id = group.get("home_room_id")
                if home_room_id is not None:
                    room = room_by_id.get(home_room_id)
                    if (room
                            and home_room_id not in used_rooms_by_timeslot[t]
                            and room["capacity"] >= group["student_count"]):
                        assigned_room = home_room_id

                # Fallback: any free room with sufficient capacity.
                if assigned_room is None:
                    for room in rooms:
                        if (room["id"] not in used_rooms_by_timeslot[t]
                                and room["capacity"] >= group["student_count"]):
                            assigned_room = room["id"]
                            break

            if assigned_room is not None:
                used_rooms_by_timeslot[t].add(assigned_room)

            schedule_entries.append(
                {
                    "timeslot_id": t,
                    "tea_assignment_id": assignment_id,
                    "room_id": assigned_room,
                }
            )

    return schedule_entries


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_hill_climbing(data: dict) -> dict:
    """
    Main entry point for the Hill Climbing algorithm, using Random Restarts
    to escape local optima, within an overall time budget.

    Parameters
    ----------
    data : dict
        The exact dict returned by data_access.fetch_all_data().

    Returns
    -------
    dict shaped EXACTLY like csp.solver.run_csp()'s return value:
        {
            "algorithm": "HILL_CLIMBING",
            "status": "COMPLETED" | "NO_DATA",
            "score": float,
            "schedule_entries": [ {...}, ... ],
        }
    """
    timeslots = data["timeslots"]
    teacher_assignments = data["teacher_assignments"]

    if not timeslots or not teacher_assignments:
        return {
            "algorithm": ALGO_HILL_CLIMBING,
            "status": "NO_DATA",
            "score": None,
            "schedule_entries": [],
        }

    lookups = _build_lookup_maps(data)
    timeslot_ids = [ts["id"] for ts in timeslots]

    deadline = time.perf_counter() + HILL_CLIMBING_TIME_BUDGET_SECONDS

    best_schedule = None
    best_score = None
    restart_count = 0

    while time.perf_counter() < deadline:
        restart_count += 1
        initial_schedule = _generate_initial_schedule(data, lookups, timeslot_ids)
        climbed_schedule, climbed_score = _climb(
            initial_schedule, data, lookups, timeslot_ids, deadline
        )

        if best_score is None or climbed_score < best_score:
            best_schedule = climbed_schedule
            best_score = climbed_score

        if best_score == 0:
            break

    schedule_entries = _assign_rooms(best_schedule, data, timeslot_ids)

    return {
        "algorithm": ALGO_HILL_CLIMBING,
        "status": "COMPLETED",
        "score": best_score,
        "schedule_entries": schedule_entries,
    }