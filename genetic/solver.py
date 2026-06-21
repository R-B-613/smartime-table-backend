"""
genetic/solver.py

Genetic Algorithm (GA) solver for the timetabling problem.

This module does NOT touch the database directly - it receives plain
Python data (as fetched by data_access.fetch_all_data()) and returns a
plain Python result dict, in the EXACT SAME SHAPE as csp.solver.run_csp()
and hill_climbing.solver.run_hill_climbing(), so it can be measured/saved/
compared identically.

-------------------------------------------------------------------------
DESIGN DECISIONS (see project book for full rationale)
-------------------------------------------------------------------------
- Population size: 50 individuals (tunable - see POPULATION_SIZE below)
- Individual representation: SAME as Hill Climbing -
      {assignment_id: [timeslot_id, ...]}
  one timeslot per weekly hour the assignment requires.
- Initial population: each individual generated independently using the
  same conflict-aware random method as Hill Climbing's
  _generate_initial_schedule.
- Fitness/scoring: the SAME unified scoring formula as CSP/Hill Climbing
  (scoring_config.py weights) - GA, like Hill Climbing, has no structural
  enforcement, so structural conflicts (teacher/group double-booking,
  weekly_hours correctness) must be scored too, not just data-driven
  constraints/preferences.
- Selection: TOURNAMENT SELECTION - to pick one parent, sample a small
  random subset of the population, the best individual in that subset
  wins. Chosen over a deterministic best-N/next-N pairing scheme because
  it better preserves population diversity (weaker individuals retain a
  real, if reduced, chance of reproducing) and is a standard, citable GA
  technique.
- Crossover: per-assignment inheritance - for each assignment_id, the
  child inherits that assignment's ENTIRE timeslot list from either
  parent A or parent B (random choice per assignment). This can introduce
  new conflicts where the two parents' choices don't align - expected,
  and handled the same way initial-population conflicts are handled
  (selection pressure + mutation over generations).
- Mutation: conceptually identical to Hill Climbing's neighbor-move -
  one assignment's one weekly-hour slot is moved to a different random
  timeslot. Applied probabilistically (MUTATION_RATE) to each child.
- Elitism: ENABLED - the best ELITE_COUNT individuals from each
  generation are copied UNCHANGED into the next generation, so the best
  score ever found cannot regress due to unlucky crossover/mutation in a
  later generation.
- Stopping condition: GA_TIME_BUDGET_SECONDS (60s, matching CSP and Hill
  Climbing for a fair comparison), with early exit if a perfect score (0)
  is found in any generation.

TODO (explicitly deferred for v1, same as CSP and Hill Climbing):
    - Room capacity / room-type matching (room assignment, like the other
      two algorithms, is decided AFTER the timeslot schedule is finalized)
    - sync_block_identity handling
    - Full preference scoring (gaps/free-day/consecutive/early-finish) -
      only the min/max hours-range preference check is implemented.
-------------------------------------------------------------------------
"""

import random
import time

from scoring_config import (
    HARD_CONSTRAINT_PENALTY,
    SOFT_CONSTRAINT_WEIGHT_MULTIPLIER,
    OUTSIDE_HOURS_RANGE_PENALTY_PER_HOUR,
    ALGO_GENETIC,
)

# Overall time budget across ALL generations combined - matches CSP and
# Hill Climbing for a fair comparison. Tune independently later.
GA_TIME_BUDGET_SECONDS = 60.0

POPULATION_SIZE = 50

# Number of individuals sampled per tournament when selecting a parent.
TOURNAMENT_SIZE = 5

# Number of best individuals copied unchanged into the next generation.
ELITE_COUNT = 2

# Probability that a given child undergoes mutation after crossover.
MUTATION_RATE = 0.3


# ---------------------------------------------------------------------------
# Lookup maps (identical structure to csp/solver.py and hill_climbing/solver.py)
# ---------------------------------------------------------------------------

def _build_lookup_maps(data: dict):
    curriculum_requirements = data["curriculum_requirements"]
    teacher_assignments = data["teacher_assignments"]
    teacher_constraints = data["teacher_constraints"]
    teacher_preferences = data["teacher_preferences"]

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

    return {
        "requirement_by_id": requirement_by_id,
        "assignment_by_id": assignment_by_id,
        "assignments_by_teacher": assignments_by_teacher,
        "assignments_by_group": assignments_by_group,
        "constraint_by_teacher_timeslot": constraint_by_teacher_timeslot,
        "preferences_by_teacher": preferences_by_teacher,
    }


# ---------------------------------------------------------------------------
# Initial individual generation (identical method to Hill Climbing's
# _generate_initial_schedule - reused here to build each population member)
# ---------------------------------------------------------------------------

def _generate_random_individual(data, lookups, timeslot_ids):
    """
    Builds ONE candidate schedule, trying to avoid the most obvious
    conflicts (teacher/group double-booking) when a non-conflicting slot
    is conveniently available - same approach as Hill Climbing's initial
    schedule generation, reused here to build each population member.

    Returns a schedule dict: {assignment_id: [timeslot_id, ...]}
    """
    teacher_assignments = data["teacher_assignments"]
    requirement_by_id = lookups["requirement_by_id"]

    schedule = {}
    used_timeslots_by_teacher = {}
    used_timeslots_by_group = {}

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
            candidate = None
            for _attempt in range(10):
                t = random.choice(timeslot_ids)
                if t not in teacher_used and t not in group_used:
                    candidate = t
                    break
            if candidate is None:
                candidate = random.choice(timeslot_ids)

            chosen_timeslots.append(candidate)
            teacher_used.add(candidate)
            group_used.add(candidate)

        schedule[ta["id"]] = chosen_timeslots

    return schedule


def _generate_initial_population(data, lookups, timeslot_ids, population_size):
    return [
        _generate_random_individual(data, lookups, timeslot_ids)
        for _ in range(population_size)
    ]


# ---------------------------------------------------------------------------
# Scoring (identical formula to csp/solver.py and hill_climbing/solver.py -
# see hill_climbing/solver.py's module docstring for why structural
# conflicts must be scored here too, unlike in CSP)
# ---------------------------------------------------------------------------

def _score_schedule(schedule, data, lookups):
    """
    Computes the FULL unified penalty score for a candidate schedule.
    Lower is better, 0 = perfect. Identical formula to Hill Climbing's
    _score_schedule (kept as a separate copy in this module rather than
    a shared import, to keep each algorithm folder self-contained and
    easy to read independently - see project notes on structure).
    """
    total_penalty = 0.0

    teacher_assignments = data["teacher_assignments"]
    requirement_by_id = lookups["requirement_by_id"]
    constraint_by_teacher_timeslot = lookups["constraint_by_teacher_timeslot"]
    preferences_by_teacher = lookups["preferences_by_teacher"]

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

    # ---- Data-driven: teacher_preferences (hours range only, for now) ----
    assignments_by_teacher = lookups["assignments_by_teacher"]
    for teacher_id, assignment_ids in assignments_by_teacher.items():
        prefs = preferences_by_teacher.get(teacher_id)
        if prefs is None:
            continue

        total_hours = sum(len(schedule[a_id]) for a_id in assignment_ids)

        if prefs["min_hours"] is not None and total_hours < prefs["min_hours"]:
            total_penalty += (prefs["min_hours"] - total_hours) * OUTSIDE_HOURS_RANGE_PENALTY_PER_HOUR
        if prefs["max_hours"] is not None and total_hours > prefs["max_hours"]:
            total_penalty += (total_hours - prefs["max_hours"]) * OUTSIDE_HOURS_RANGE_PENALTY_PER_HOUR

        # TODO: same as CSP/Hill Climbing - gap/free-day/consecutive/
        # early-finish checks not yet implemented.

    return total_penalty


# ---------------------------------------------------------------------------
# Selection: tournament selection
# ---------------------------------------------------------------------------

def _tournament_select(population_with_scores):
    """
    Picks ONE parent via tournament selection: samples TOURNAMENT_SIZE
    individuals at random from the population, returns the one with the
    best (lowest) score among that sample.

    population_with_scores: list of (schedule, score) tuples.
    """
    contenders = random.sample(population_with_scores, TOURNAMENT_SIZE)
    contenders.sort(key=lambda pair: pair[1])
    return contenders[0][0]  # the schedule (not the score) of the winner


# ---------------------------------------------------------------------------
# Crossover: per-assignment inheritance
# ---------------------------------------------------------------------------

def _crossover(parent_a, parent_b):
    """
    Builds one child schedule: for each assignment_id, the child inherits
    that assignment's ENTIRE timeslot list from either parent_a or
    parent_b (random choice per assignment, independent each time).
    """
    child = {}
    for assignment_id in parent_a:
        if random.random() < 0.5:
            child[assignment_id] = list(parent_a[assignment_id])
        else:
            child[assignment_id] = list(parent_b[assignment_id])
    return child


# ---------------------------------------------------------------------------
# Mutation: identical idea to Hill Climbing's single-session move
# ---------------------------------------------------------------------------

def _mutate(schedule, timeslot_ids):
    """
    Mutates ONE schedule IN PLACE: picks one random assignment and one of
    its weekly-hour slots, and moves it to a different random timeslot.
    Conceptually identical to hill_climbing.solver._get_random_neighbor,
    but applied directly to a single individual as part of GA's mutation
    step, rather than used to generate/compare candidate neighbors.
    """
    assignment_id = random.choice(list(schedule.keys()))
    hour_index = random.randrange(len(schedule[assignment_id]))
    schedule[assignment_id][hour_index] = random.choice(timeslot_ids)


# ---------------------------------------------------------------------------
# Room assignment (identical simple v1 approach to CSP/Hill Climbing)
# ---------------------------------------------------------------------------

def _assign_rooms(schedule, data, timeslot_ids):
    rooms = data["rooms"]
    schedule_entries = []

    used_rooms_by_timeslot = {t: set() for t in timeslot_ids}

    for assignment_id, timeslots in schedule.items():
        for t in timeslots:
            assigned_room = None
            for room in rooms:
                if room["id"] not in used_rooms_by_timeslot[t]:
                    assigned_room = room["id"]
                    break

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

def run_genetic(data: dict) -> dict:
    """
    Main entry point for the Genetic Algorithm.

    Parameters
    ----------
    data : dict
        The exact dict returned by data_access.fetch_all_data().

    Returns
    -------
    dict shaped EXACTLY like csp.solver.run_csp() and
    hill_climbing.solver.run_hill_climbing()'s return values:
        {
            "algorithm": "GENETIC",
            "status": "COMPLETED" | "NO_DATA",
            "score": float,
            "schedule_entries": [ {...}, ... ],
        }
    """
    timeslots = data["timeslots"]
    teacher_assignments = data["teacher_assignments"]

    if not timeslots or not teacher_assignments:
        return {
            "algorithm": ALGO_GENETIC,
            "status": "NO_DATA",
            "score": None,
            "schedule_entries": [],
        }

    lookups = _build_lookup_maps(data)
    timeslot_ids = [ts["id"] for ts in timeslots]

    deadline = time.perf_counter() + GA_TIME_BUDGET_SECONDS

    population = _generate_initial_population(data, lookups, timeslot_ids, POPULATION_SIZE)

    best_schedule = None
    best_score = None
    generation_count = 0

    while time.perf_counter() < deadline:
        generation_count += 1

        # Score the whole population once per generation.
        population_with_scores = [
            (individual, _score_schedule(individual, data, lookups))
            for individual in population
        ]
        population_with_scores.sort(key=lambda pair: pair[1])

        # Track the best individual ever seen (across all generations).
        generation_best_schedule, generation_best_score = population_with_scores[0]
        if best_score is None or generation_best_score < best_score:
            best_schedule = generation_best_schedule
            best_score = generation_best_score

        if best_score == 0:
            break

        # Elitism: carry the best ELITE_COUNT individuals over unchanged.
        next_population = [
            individual for individual, _score in population_with_scores[:ELITE_COUNT]
        ]

        # Fill the rest of the next generation via selection + crossover + mutation.
        while len(next_population) < POPULATION_SIZE:
            if time.perf_counter() >= deadline:
                break

            parent_a = _tournament_select(population_with_scores)
            parent_b = _tournament_select(population_with_scores)

            child = _crossover(parent_a, parent_b)

            if random.random() < MUTATION_RATE:
                _mutate(child, timeslot_ids)

            next_population.append(child)

        population = next_population

    schedule_entries = _assign_rooms(best_schedule, data, timeslot_ids)

    return {
        "algorithm": ALGO_GENETIC,
        "status": "COMPLETED",
        "score": best_score,
        "schedule_entries": schedule_entries,
    }