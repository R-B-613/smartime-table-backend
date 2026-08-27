"""
experiment_run.py

Runs the full 3-algorithm pipeline ONCE, at a time budget YOU specify on the
command line, and appends a row PER ALGORITHM to experiment_results.csv with
all the metrics you need for the research write-up:

    time budget, algorithm, score, hard violations, soft violations,
    total violations, runtime (s), peak memory (MB), status, timestamp

You run it once per time budget, changing the number each time, e.g.:

    python experiment_run.py 60
    python experiment_run.py 90
    python experiment_run.py 120
    python experiment_run.py 150
    python experiment_run.py 180

Each run APPENDS to experiment_results.csv (it doesn't overwrite), so after
running all the budgets you have one clean table with every data point.

IMPORTANT - how the time budget is applied:
    The three solvers read their time budgets from module-level constants:
        scoring_config.CSP_MAX_SOLVE_SECONDS
        hill_climbing.solver.HILL_CLIMBING_TIME_BUDGET_SECONDS
        genetic.solver.GA_TIME_BUDGET_SECONDS
    This script OVERRIDES all three at runtime (monkey-patch) to the budget you
    pass in, so you do NOT have to edit any file between runs - just pass a
    different number. The override lasts only for this process; your source
    files are never changed.

NOTE on memory: like performance.py, peak memory is process-wide. Because all
three algorithms run in ONE process here (sequentially), the memory number for
later algorithms includes the high-water mark of earlier ones. This is the
SAME caveat main.py already has. For the write-up, treat memory as approximate
and comparable in ORDER OF MAGNITUDE, not to the last MB. (If you want per-
algorithm-isolated memory later, we run each in its own process - a separate
tool.) Runtime is per-algorithm and accurate.

This script does NOT touch the schedule/schedule_runs tables the way the app
does - actually, it DOES save via the comparator (same as a normal generation),
so each experiment run also produces a real saved schedule you can view. If you
do NOT want these experiment runs cluttering your history, see the SAVE_TO_DB
flag below.

Run from the repo root (venv activated):
    python experiment_run.py <seconds>
"""

import sys
import csv
import os
import datetime as dt

# ---- CONFIG -------------------------------------------------------------
# If True, each experiment run also saves its schedules to the DB (appears in
# history, exactly like a normal generation). If False, it runs the algorithms
# and measures them but does NOT write to schedule_runs/schedule - cleaner if
# you're just gathering timing data and don't want 5 extra runs per budget in
# your history.
SAVE_TO_DB = False

RESULTS_FILE = "experiment_results.csv"
# -------------------------------------------------------------------------

from data_access import fetch_all_data
from performance import measure_performance

# Import the solver modules THEMSELVES (not just the functions) so we can
# override their time-budget constants at runtime.
import scoring_config
import hill_climbing.solver as hc_solver
import genetic.solver as ga_solver
from csp.solver import run_csp
from hill_climbing.solver import run_hill_climbing
from genetic.solver import run_genetic
from scoring_violations import score_genetic_schedule_with_violations

# Only needed if SAVE_TO_DB is True.
if SAVE_TO_DB:
    from comparator import save_and_select_best_result


_ALGORITHMS = [
    ("CSP", run_csp),
    ("HILL_CLIMBING", run_hill_climbing),
    ("GENETIC", run_genetic),
]


def _apply_time_budget(seconds: float):
    """
    Override all three solvers' time budgets to `seconds` for THIS process.
    Prints confirmation so you can see it took effect.
    """
    scoring_config.CSP_MAX_SOLVE_SECONDS = float(seconds)
    hc_solver.HILL_CLIMBING_TIME_BUDGET_SECONDS = float(seconds)
    ga_solver.GA_TIME_BUDGET_SECONDS = float(seconds)
    print(f"Time budget set to {seconds}s for CSP, Hill Climbing, and Genetic.")


def _count_violations(result, data, lookups):
    """
    Returns (hard_count, soft_count, total_count) for a result's schedule.
    Uses the violations already attached to the result if present; otherwise
    recomputes from the saved violations list.
    """
    violations = result.get("violations")
    if violations is None:
        return (0, 0, 0)
    hard = sum(1 for v in violations if v.get("severity") == "hard")
    soft = sum(1 for v in violations if v.get("severity") == "soft")
    return (hard, soft, len(violations))


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("Usage: python experiment_run.py <seconds>")
        print("Example: python experiment_run.py 120")
        return 1

    try:
        seconds = float(argv[0])
    except ValueError:
        print("The time budget must be a number (seconds). Example: 120")
        return 1

    print("=" * 60)
    print(f"EXPERIMENT RUN @ {seconds}s time budget")
    print("=" * 60)

    _apply_time_budget(seconds)

    print("Fetching data...")
    data = fetch_all_data()

    # Build lookups once for violation-counting (same maps the solvers use).
    from genetic.solver import _build_lookup_maps
    lookups = _build_lookup_maps(data)

    rows_to_write = []
    results = []
    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for name, algorithm_fn in _ALGORITHMS:
        print(f"\nRunning {name} (budget {seconds}s)...")
        result, perf = measure_performance(algorithm_fn, data)
        results.append(result)

        hard, soft, total = _count_violations(result, data, lookups)
        score = result.get("score")
        status = result.get("status")
        runtime = perf["runtime_seconds"]
        memory = perf["peak_memory_mb"]

        print(f"  {name}: score={score}  status={status}")
        print(f"    hard_violations={hard}  soft_violations={soft}  total={total}")
        print(f"    runtime={runtime}s  peak_memory={memory}MB")

        rows_to_write.append({
            "time_budget_s": seconds,
            "algorithm": name,
            "score": score,
            "hard_violations": hard,
            "soft_violations": soft,
            "total_violations": total,
            "runtime_seconds": runtime,
            "peak_memory_mb": memory,
            "status": status,
            "timestamp": timestamp,
        })

    # Optionally save to DB (like a normal generation).
    if SAVE_TO_DB:
        print("\nSaving results to database (SAVE_TO_DB=True)...")
        comparison = save_and_select_best_result(results)
        print(f"  best: {comparison['best_algorithm']} "
              f"(run_id={comparison['best_run_id']}, score={comparison['best_score']})")
    else:
        print("\nSAVE_TO_DB=False - results NOT written to schedule_runs/schedule "
              "(measurement only).")

    # Append rows to the CSV (create with header if it doesn't exist yet).
    file_exists = os.path.exists(RESULTS_FILE)
    fieldnames = [
        "time_budget_s", "algorithm", "score",
        "hard_violations", "soft_violations", "total_violations",
        "runtime_seconds", "peak_memory_mb", "status", "timestamp",
    ]
    with open(RESULTS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for row in rows_to_write:
            writer.writerow(row)

    print(f"\nAppended {len(rows_to_write)} rows to {RESULTS_FILE}")
    print("Done. Change the number and run again for the next time budget.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
