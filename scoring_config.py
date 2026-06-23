"""
scoring_config.py

Central place for every weight/penalty constant used to score a generated
schedule. ALL THREE algorithms (CSP, Genetic Algorithm, Hill Climbing) import
from this file, so that the final scores they produce are comparable on the
same scale.

Design idea (as discussed):
- Hard constraints get a very large penalty if violated, so the algorithms
  always prefer to satisfy them first.
- Soft constraints (from teacher_constraints with type='soft') use their
  own per-row `weight` column (1-10) from the DB.
- teacher_preferences has no weight column in the DB on purpose - the
  weights for each kind of preference are defined here in code instead,
  so they can be tuned without touching the database.
"""

# ---------------------------------------------------------------------------
# Hard constraint penalty
# ---------------------------------------------------------------------------
# Used for any hard rule violation (e.g. a hard row in teacher_constraints).
# Kept extremely high on purpose: even satisfying *every* soft constraint
# and preference perfectly should never be able to outweigh breaking one
# hard constraint.
HARD_CONSTRAINT_PENALTY = 10_000

# ---------------------------------------------------------------------------
# Soft constraints (teacher_constraints where constraint_type = 'soft')
# ---------------------------------------------------------------------------
# These rows already carry their own weight (1-10) from the DB.
# This multiplier just lets us scale all soft-constraint penalties up/down
# relative to preference penalties below, without editing DB rows.
SOFT_CONSTRAINT_WEIGHT_MULTIPLIER = 1

# ---------------------------------------------------------------------------
# teacher_preferences weights (no DB column -> defined here)
# ---------------------------------------------------------------------------
# Each of these is multiplied by the teacher's own priority_* value (1-5)
# from teacher_preferences, then added as a penalty if the preference is
# not met for that teacher in the generated schedule.
PREFERENCE_WEIGHTS = {
    "early_finish": 2,      # priority_early_finish
    "no_gaps": 2,            # priority_no_gaps
    "free_day": 3,            # priority_free_day
    "consecutive": 2,        # priority_consecutive / preferred_consecutive
}

# Penalty applied per hour a teacher is outside their [min_hours, max_hours]
# range from teacher_preferences.
OUTSIDE_HOURS_RANGE_PENALTY_PER_HOUR = 5

# Penalty for scheduling more than 1 hour of the same subject on the same
# day for the same student group (encourages distribution across the week).
SUBJECT_DISTRIBUTION_PENALTY_PER_EXTRA_HOUR = 5

# ---------------------------------------------------------------------------
# CSP solver settings
# ---------------------------------------------------------------------------
CSP_MAX_SOLVE_SECONDS = 60.0

# ---------------------------------------------------------------------------
# Algorithm name constants (must match schedule_runs.algorithm CHECK constraint)
# ---------------------------------------------------------------------------
ALGO_CSP = "CSP"
ALGO_GENETIC = "GENETIC"
ALGO_HILL_CLIMBING = "HILL_CLIMBING"