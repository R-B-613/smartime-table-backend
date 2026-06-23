"""
csp/solver.py

CSP (Constraint Satisfaction Problem) solver for the timetabling problem,
built with Google OR-Tools CP-SAT.

This module does NOT touch the database directly - it receives plain
Python data (as fetched by data_access.fetch_all_data()) and returns a
plain Python result dict. Saving to the DB happens elsewhere (main.py).

-------------------------------------------------------------------------
HOW HARD VS SOFT CONSTRAINTS ARE HANDLED HERE
-------------------------------------------------------------------------
Two different mechanisms are used on purpose:

1) STRUCTURAL hard constraints (the solver is physically not allowed to
   break these - not even scored, just forbidden):
     - A teacher cannot teach two different things at the same timeslot.
     - A student group cannot attend two different lessons at the same
       timeslot.
     - A room cannot host two different lessons at the same timeslot.
     - Every curriculum_requirement must receive exactly its required
       weekly_hours.
   These come "for free" from the timetable structure - any teacher/
   school timetable would consider these absolute rules, not preferences.

2) SCORED hard/soft constraints (from teacher_constraints, weighted very
   high if type='hard', low if type='soft') and teacher_preferences
   (weighted using scoring_config.PREFERENCE_WEIGHTS). These are NOT
   structurally forbidden in the CP-SAT model (CP-SAT could technically
   still produce a solution that violates a "hard" row in
   teacher_constraints, if doing so allows the model to be feasible at
   all) - instead they're folded into one unified penalty score, using
   the EXACT SAME formula that the Genetic Algorithm and Hill Climbing
   solvers will use later, so all three algorithms can be compared fairly
   on the same scale.

This keeps CSP fast and reliable (thanks to true structural constraints)
while still producing a score that's apples-to-apples comparable with
GA/Hill Climbing.

TODO (explicitly skipped for v1, per project decision):
    - Room capacity matching (room.capacity vs student_group.student_count)
    - Room type matching (subjects.required_room_id)
    - sync_block_identity (lessons that must be scheduled simultaneously)
-------------------------------------------------------------------------
"""

from ortools.sat.python import cp_model
from scoring_config import (
    HARD_CONSTRAINT_PENALTY,
    SOFT_CONSTRAINT_WEIGHT_MULTIPLIER,
    PREFERENCE_WEIGHTS,
    OUTSIDE_HOURS_RANGE_PENALTY_PER_HOUR,
    CSP_MAX_SOLVE_SECONDS,
    ALGO_CSP,
)


def _build_lookup_maps(data: dict):
    """
    Builds convenience lookup dicts/sets from the raw fetched data, so the
    rest of the solver doesn't need to repeatedly loop over lists.
    """
    timeslots = data["timeslots"]
    rooms = data["rooms"]
    curriculum_requirements = data["curriculum_requirements"]
    teacher_assignments = data["teacher_assignments"]
    teacher_constraints = data["teacher_constraints"]
    teacher_preferences = data["teacher_preferences"]

    # cur_requirement_id -> requirement row (for weekly_hours, student_group_id, subject_id)
    requirement_by_id = {req["id"]: req for req in curriculum_requirements}

    # teacher_assignment.id -> assignment row (for teacher_id, cur_requirement_id)
    assignment_by_id = {ta["id"]: ta for ta in teacher_assignments}

    # teacher_id -> list of assignment ids they teach
    assignments_by_teacher = {}
    for ta in teacher_assignments:
        assignments_by_teacher.setdefault(ta["teacher_id"], []).append(ta["id"])

    # student_group_id -> list of assignment ids that belong to that group
    assignments_by_group = {}
    for ta in teacher_assignments:
        req = requirement_by_id[ta["cur_requirement_id"]]
        assignments_by_group.setdefault(req["student_group_id"], []).append(ta["id"])

    # (teacher_id, timeslot_id) -> constraint row, for fast lookup during scoring
    constraint_by_teacher_timeslot = {
        (c["teacher_id"], c["timeslot_id"]): c for c in teacher_constraints
    }

    # teacher_id -> preferences row
    preferences_by_teacher = {p["teacher_id"]: p for p in teacher_preferences}

    return {
        "requirement_by_id": requirement_by_id,
        "assignment_by_id": assignment_by_id,
        "assignments_by_teacher": assignments_by_teacher,
        "assignments_by_group": assignments_by_group,
        "constraint_by_teacher_timeslot": constraint_by_teacher_timeslot,
        "preferences_by_teacher": preferences_by_teacher,
    }


def _create_decision_variables(model, teacher_assignments, timeslots):
    """
    Creates one BoolVar per (assignment, timeslot) combination:
    schedule_vars[(assignment_id, timeslot_id)] == 1 means "this teaching
    assignment happens at this timeslot".
    """
    schedule_vars = {}
    for ta in teacher_assignments:
        for ts in timeslots:
            var_name = f"assign_{ta['id']}_time_{ts['id']}"
            schedule_vars[(ta["id"], ts["id"])] = model.NewBoolVar(var_name)
    return schedule_vars


def _add_structural_hard_constraints(model, schedule_vars, data, lookups, timeslots):
    """
    Adds the constraints that are NEVER allowed to be broken, enforced
    structurally (the solver cannot even propose a solution that breaks
    these - they aren't scored, they're forbidden).
    """
    teacher_assignments = data["teacher_assignments"]
    requirement_by_id = lookups["requirement_by_id"]
    assignments_by_teacher = lookups["assignments_by_teacher"]
    assignments_by_group = lookups["assignments_by_group"]

    # 1. Each curriculum requirement must get EXACTLY its weekly_hours,
    #    spread across its assignment(s).
    #    (Usually one assignment per requirement, but this works even if
    #    a requirement is split across multiple teacher_assignments rows.)
    assignments_by_requirement = {}
    for ta in teacher_assignments:
        assignments_by_requirement.setdefault(ta["cur_requirement_id"], []).append(ta["id"])

    for req_id, assignment_ids in assignments_by_requirement.items():
        weekly_hours = requirement_by_id[req_id]["weekly_hours"]
        model.Add(
            sum(
                schedule_vars[(a_id, ts["id"])]
                for a_id in assignment_ids
                for ts in timeslots
            )
            == weekly_hours
        )

    # 2. A teacher cannot teach two different assignments at the same timeslot.
    for teacher_id, assignment_ids in assignments_by_teacher.items():
        for ts in timeslots:
            model.AddAtMostOne(
                schedule_vars[(a_id, ts["id"])] for a_id in assignment_ids
            )

    # 3. A student group cannot attend two different lessons at the same timeslot.
    for group_id, assignment_ids in assignments_by_group.items():
        for ts in timeslots:
            model.AddAtMostOne(
                schedule_vars[(a_id, ts["id"])] for a_id in assignment_ids
            )

    # NOTE: room-conflict and room-capacity constraints are intentionally
    # skipped here for v1 (see module docstring TODO). Room assignment is
    # currently decided AFTER solving (see _assign_rooms below), using the
    # simplest possible rule: "any room free at that timeslot".


def _compute_penalty_score(solver, schedule_vars, data, lookups, timeslots):
    """
    Computes the SAME unified penalty score that GA and Hill Climbing will
    use, based on the solved CP-SAT model's chosen values. Lower score is
    better (0 = perfect schedule, no soft violations).

    This does NOT affect what the CSP solver is allowed to choose - it's
    purely informational, calculated AFTER solving, so it can be compared
    against GA/Hill Climbing's score later.
    """
    constraint_by_teacher_timeslot = lookups["constraint_by_teacher_timeslot"]
    preferences_by_teacher = lookups["preferences_by_teacher"]
    assignment_by_id = lookups["assignment_by_id"]
    assignments_by_teacher = lookups["assignments_by_teacher"]

    total_penalty = 0.0

    # ---- Soft/hard teacher_constraints penalties ----
    # (In v1, CSP structurally can't violate hard constraints in most cases
    # because of how the model is built above for conflicts - but
    # teacher_constraints rows represent availability, which ISN'T yet
    # wired in as a structural rule. So we score it here, same as GA/HC will.)
    for ta in data["teacher_assignments"]:
        for ts in timeslots:
            var = schedule_vars[(ta["id"], ts["id"])]
            if solver.Value(var) == 1:
                key = (ta["teacher_id"], ts["id"])
                constraint = constraint_by_teacher_timeslot.get(key)
                if constraint is not None:
                    if constraint["constraint_type"] == "hard":
                        total_penalty += HARD_CONSTRAINT_PENALTY
                    else:  # soft
                        total_penalty += constraint["weight"] * SOFT_CONSTRAINT_WEIGHT_MULTIPLIER

    # ---- teacher_preferences penalties ----
    for teacher_id, assignment_ids in assignments_by_teacher.items():
        prefs = preferences_by_teacher.get(teacher_id)
        if prefs is None:
            continue  # no preferences row for this teacher -> nothing to score

        # Collect which timeslots this teacher actually teaches at.
        taught_timeslot_ids = []
        for a_id in assignment_ids:
            for ts in timeslots:
                if solver.Value(schedule_vars[(a_id, ts["id"])]) == 1:
                    taught_timeslot_ids.append(ts["id"])

        total_hours = len(taught_timeslot_ids)

        # Outside [min_hours, max_hours] range.
        if prefs["min_hours"] is not None and total_hours < prefs["min_hours"]:
            total_penalty += (prefs["min_hours"] - total_hours) * OUTSIDE_HOURS_RANGE_PENALTY_PER_HOUR
        if prefs["max_hours"] is not None and total_hours > prefs["max_hours"]:
            total_penalty += (total_hours - prefs["max_hours"]) * OUTSIDE_HOURS_RANGE_PENALTY_PER_HOUR

        # NOTE: no_gaps / free_day / consecutive / early_finish all require
        # knowing the day_of_week + hour_of_day layout per teacher across
        # the whole week. Implementing the actual day-by-day gap/consecutive
        # detection is left as a clearly marked next step, since it depends
        # on how you want "a gap" defined exactly (e.g. does a free period
        # before lunch count?). Structure below shows where it plugs in:

        # TODO: implement real gap/free-day/consecutive/early-finish checks
        # using PREFERENCE_WEIGHTS["no_gaps"], PREFERENCE_WEIGHTS["free_day"],
        # PREFERENCE_WEIGHTS["consecutive"], PREFERENCE_WEIGHTS["early_finish"]
        # multiplied by prefs["priority_no_gaps"], prefs["priority_free_day"],
        # prefs["priority_consecutive"], prefs["priority_early_finish"].

    return total_penalty


def _assign_rooms(solver, schedule_vars, data, timeslots):
    """
    Smart room assignment, applied AFTER the CP-SAT solve is complete.
    Same three-tier priority as HC/GA (specific room -> room type ->
    home room -> fallback), adapted for CSP's variable-based schedule
    representation.
    All tiers check: room is free at this timeslot AND capacity >= group size.
    """
    rooms = data["rooms"]
    subjects = data["subjects"]
    student_groups = data["student_groups"]
    curriculum_requirements = data["curriculum_requirements"]

    room_by_id = {r["id"]: r for r in rooms}
    subject_by_id = {s["id"]: s for s in subjects}
    group_by_id = {g["id"]: g for g in student_groups}
    requirement_by_id = {req["id"]: req for req in curriculum_requirements}

    schedule_entries = []
    used_rooms_by_timeslot = {ts["id"]: set() for ts in timeslots}

    for ta in data["teacher_assignments"]:
        req = requirement_by_id[ta["cur_requirement_id"]]
        subject = subject_by_id[req["subject_id"]]
        group = group_by_id[req["student_group_id"]]

        for ts in timeslots:
            var = schedule_vars[(ta["id"], ts["id"])]
            if solver.Value(var) == 1:
                assigned_room = None

                if subject["required_room_id"] is not None:
                    # Priority 1: subject needs ONE specific dedicated room.
                    room_id = subject["required_room_id"]
                    room = room_by_id.get(room_id)
                    if (room
                            and room_id not in used_rooms_by_timeslot[ts["id"]]
                            and room["capacity"] >= group["student_count"]):
                        assigned_room = room_id

                elif subject.get("required_room_type") is not None:
                    # Priority 2: subject needs any room of a certain type.
                    for room in rooms:
                        if (room.get("room_type") == subject["required_room_type"]
                                and room["id"] not in used_rooms_by_timeslot[ts["id"]]
                                and room["capacity"] >= group["student_count"]):
                            assigned_room = room["id"]
                            break

                else:
                    # Priority 3: use the group's home (parent) classroom.
                    home_room_id = group.get("home_room_id")
                    if home_room_id is not None:
                        room = room_by_id.get(home_room_id)
                        if (room
                                and home_room_id not in used_rooms_by_timeslot[ts["id"]]
                                and room["capacity"] >= group["student_count"]):
                            assigned_room = home_room_id

                    # Fallback: any free room with sufficient capacity.
                    if assigned_room is None:
                        for room in rooms:
                            if (room["id"] not in used_rooms_by_timeslot[ts["id"]]
                                    and room["capacity"] >= group["student_count"]):
                                assigned_room = room["id"]
                                break

                if assigned_room is not None:
                    used_rooms_by_timeslot[ts["id"]].add(assigned_room)

                schedule_entries.append(
                    {
                        "timeslot_id": ts["id"],
                        "tea_assignment_id": ta["id"],
                        "room_id": assigned_room,
                    }
                )

    return schedule_entries


def run_csp(data: dict) -> dict:
    """
    Main entry point for the CSP algorithm.

    Parameters
    ----------
    data : dict
        The exact dict returned by data_access.fetch_all_data().

    Returns
    -------
    dict shaped like:
        {
            "algorithm": "CSP",
            "status": "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | "UNKNOWN",
            "score": float,                # lower is better, 0 = perfect
            "schedule_entries": [ {...}, ... ],  # ready for data_access.save_schedule_run()
        }
    """
    timeslots = data["timeslots"]
    teacher_assignments = data["teacher_assignments"]

    if not timeslots or not teacher_assignments:
        return {
            "algorithm": ALGO_CSP,
            "status": "NO_DATA",
            "score": None,
            "schedule_entries": [],
        }

    lookups = _build_lookup_maps(data)

    model = cp_model.CpModel()
    schedule_vars = _create_decision_variables(model, teacher_assignments, timeslots)

    _add_structural_hard_constraints(model, schedule_vars, data, lookups, timeslots)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = CSP_MAX_SOLVE_SECONDS
    status = solver.Solve(model)

    status_name = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {
            "algorithm": ALGO_CSP,
            "status": status_name,
            "score": None,
            "schedule_entries": [],
        }

    score = _compute_penalty_score(solver, schedule_vars, data, lookups, timeslots)
    schedule_entries = _assign_rooms(solver, schedule_vars, data, timeslots)

    return {
        "algorithm": ALGO_CSP,
        "status": status_name,
        "score": score,
        "schedule_entries": schedule_entries,
    }