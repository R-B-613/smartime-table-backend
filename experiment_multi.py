"""
experiment_multi.py

Runs the full 3-algorithm pipeline N TIMES at a FIXED time budget, so you get
proper statistics for the stochastic algorithms (HC, GA) instead of a single
noisy sample. Appends every individual run to experiment_multi_results.csv AND
prints a summary table (mean, std, min, max, feasibility rate) at the end.

WHY: HC and GA are random - a single run is one sample, not "the answer". With
N runs you can report mean +/- std, which is how stochastic algorithms are
properly benchmarked. CSP is deterministic, so its N runs will be identical
(that itself is worth showing: zero variance).

USAGE (run from repo root, venv activated):
    python experiment_multi.py <seconds> <N>

Examples:
    python experiment_multi.py 120 20      # 20 runs at 120s each
    python experiment_multi.py 120 5       # quick 5-run test first

TIME COST (approx): each run ~= 2 x budget (HC + GA both use the full budget;
CSP is ~0.3s). So 20 runs at 120s ~= 20 * 240s ~= 80 minutes. Plan accordingly.

The script prints progress after every run so you can see it's alive.

NOTE on memory: still process-wide (see performance.py) - all three share one
process, so memory is NOT a clean per-algorithm number here. Runtime and
score/violations ARE reliable. For the write-up, lead with score + feasibility
+ runtime; treat memory as approximate.

Like experiment_run.py, this does NOT save schedules to the DB (measurement
only), so it won't flood your history.
"""

import sys
import csv
import os
import statistics
import datetime as dt

RESULTS_FILE = "experiment_multi_results.csv"

from data_access import fetch_all_data
from performance import measure_performance

import scoring_config
import hill_climbing.solver as hc_solver
import genetic.solver as ga_solver
from csp.solver import run_csp
from hill_climbing.solver import run_hill_climbing
from genetic.solver import run_genetic


_ALGORITHMS = [
    ("CSP", run_csp),
    ("HILL_CLIMBING", run_hill_climbing),
    ("GENETIC", run_genetic),
]


def _apply_time_budget(seconds: float):
    scoring_config.CSP_MAX_SOLVE_SECONDS = float(seconds)
    hc_solver.HILL_CLIMBING_TIME_BUDGET_SECONDS = float(seconds)
    ga_solver.GA_TIME_BUDGET_SECONDS = float(seconds)


def _count_violations(result):
    violations = result.get("violations")
    if violations is None:
        return (0, 0, 0)
    hard = sum(1 for v in violations if v.get("severity") == "hard")
    soft = sum(1 for v in violations if v.get("severity") == "soft")
    return (hard, soft, len(violations))


def _summarize(values):
    """Return (mean, std, min, max) for a list of numbers; std=0 if <2 values."""
    if not values:
        return (None, None, None, None)
    mean = statistics.mean(values)
    std = statistics.pstdev(values) if len(values) > 1 else 0.0
    return (mean, std, min(values), max(values))


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print("Usage: python experiment_multi.py <seconds> <N>")
        print("Example: python experiment_multi.py 120 20")
        return 1

    try:
        seconds = float(argv[0])
        n_runs = int(argv[1])
    except ValueError:
        print("Args must be: <seconds> (number) <N> (integer). Example: 120 20")
        return 1

    if n_runs < 1:
        print("N must be at least 1.")
        return 1

    est_minutes = (n_runs * 2 * seconds) / 60.0
    print("=" * 64)
    print(f"MULTI-RUN EXPERIMENT: {n_runs} runs @ {seconds}s budget")
    print(f"Rough time estimate: ~{est_minutes:.0f} minutes "
          f"(HC + GA each use the full budget per run)")
    print("=" * 64)

    _apply_time_budget(seconds)

    print("Fetching data once...")
    data = fetch_all_data()

    # Collect per-algorithm lists across all runs.
    stats = {
        name: {"score": [], "hard": [], "soft": [], "total": [],
               "runtime": [], "feasible": 0}
        for name, _ in _ALGORITHMS
    }

    file_exists = os.path.exists(RESULTS_FILE)
    fieldnames = [
        "run_index", "time_budget_s", "algorithm", "score",
        "hard_violations", "soft_violations", "total_violations",
        "runtime_seconds", "peak_memory_mb", "status", "timestamp",
    ]
    f = open(RESULTS_FILE, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    if not file_exists:
        writer.writeheader()

    try:
        for run_index in range(1, n_runs + 1):
            print(f"\n----- Run {run_index}/{n_runs} -----")
            timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            for name, algorithm_fn in _ALGORITHMS:
                result, perf = measure_performance(algorithm_fn, data)
                hard, soft, total = _count_violations(result)
                score = result.get("score")
                status = result.get("status")
                runtime = perf["runtime_seconds"]
                memory = perf["peak_memory_mb"]

                stats[name]["score"].append(score if score is not None else float("nan"))
                stats[name]["hard"].append(hard)
                stats[name]["soft"].append(soft)
                stats[name]["total"].append(total)
                stats[name]["runtime"].append(runtime)
                if hard == 0:
                    stats[name]["feasible"] += 1

                print(f"  {name:14s} score={score:<12} hard={hard:<3} soft={soft:<4} "
                      f"runtime={runtime:.2f}s")

                writer.writerow({
                    "run_index": run_index,
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
            f.flush()  # write after each run so a crash doesn't lose everything
    finally:
        f.close()

    # ---- Summary statistics ----
    print("\n" + "=" * 64)
    print(f"SUMMARY over {n_runs} runs @ {seconds}s")
    print("=" * 64)
    for name, _ in _ALGORITHMS:
        s = stats[name]
        sc_mean, sc_std, sc_min, sc_max = _summarize(
            [v for v in s["score"] if v == v]  # drop NaN
        )
        h_mean, h_std, h_min, h_max = _summarize(s["hard"])
        so_mean, so_std, so_min, so_max = _summarize(s["soft"])
        rt_mean, rt_std, rt_min, rt_max = _summarize(s["runtime"])
        feasible_pct = 100.0 * s["feasible"] / n_runs

        print(f"\n{name}")
        print(f"  score:    mean={sc_mean:.1f}  std={sc_std:.1f}  "
              f"min={sc_min:.1f}  max={sc_max:.1f}")
        print(f"  hard:     mean={h_mean:.2f}  std={h_std:.2f}  "
              f"min={h_min}  max={h_max}")
        print(f"  soft:     mean={so_mean:.2f}  std={so_std:.2f}  "
              f"min={so_min}  max={so_max}")
        print(f"  runtime:  mean={rt_mean:.2f}s  std={rt_std:.2f}s")
        print(f"  feasible (0 hard): {s['feasible']}/{n_runs}  ({feasible_pct:.0f}%)")

    print(f"\nAll {n_runs} runs appended to {RESULTS_FILE}")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
