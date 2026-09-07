"""
hybrid_experiment.py

Runs the HYBRID approaches N times at a fixed time budget and reports statistics,
directly comparable to the standalone experiment (experiment_multi.py).

For each run it measures FOUR things on the SAME CSP seed:
    1. CSP seed          - CSP's own feasible schedule (the baseline)
    2. HC-hybrid         - Hill Climbing seeded from CSP
    3. GA-hybrid         - Genetic Algorithm seeded from CSP (80/20)
    4. CHAIN (CSP->GA->HC) - GA-hybrid's result, then polished by HC
                            (the simple single-pass chain)

Every run appends rows to hybrid_experiment_results.csv, and a summary
(mean/std/min/max + feasibility rate) prints at the end.

USAGE (run from repo root, venv activated). DETACH IT so an SSH drop can't
kill a long run (see below):

    python hybrid_experiment.py <seconds> <N>

Examples:
    python hybrid_experiment.py 120 15
    python hybrid_experiment.py 120 3     # quick test first

RUN IT DETACHED (recommended for N>3, so SSH disconnect doesn't kill it):
    nohup python hybrid_experiment.py 120 15 > hybrid_out.log 2>&1 &
    tail -f hybrid_out.log        # watch progress; Ctrl+C stops WATCHING, not the run

TIME COST (approx): each run is CSP(~0.3s) + HC-hybrid(budget) + GA-hybrid(budget)
+ HC-polish(budget) ~= 3 x budget. So 15 runs at 120s ~= 15 * 360s ~= 90 minutes.
(If that's too long, the CHAIN's HC-polish is the extra cost; you can set
RUN_CHAIN=False below to measure just CSP/HC-hybrid/GA-hybrid at ~2x budget/run.)

NOTE on memory: process-wide (see performance.py) - not a clean per-approach
number here. Lead the write-up with score + feasibility; treat memory as
approximate.

Does NOT save schedules to the DB (measurement only) - won't touch history.
"""

import sys
import csv
import os
import statistics
import datetime as dt

# ---- CONFIG -------------------------------------------------------------
# The CHAIN (CSP->GA->HC) adds one extra budget's worth of time per run. Set to
# False to skip it and measure only CSP / HC-hybrid / GA-hybrid (faster).
RUN_CHAIN = True

# GA seeding (matches the 80/20 you chose).
SEED_FRACTION = 0.8
SEED_MUTATIONS = 3

RESULTS_FILE = "hybrid_experiment_results.csv"
# -------------------------------------------------------------------------

from data_access import fetch_all_data
from performance import measure_performance
from genetic.solver import _build_lookup_maps, _score_schedule
from scoring_violations import score_genetic_schedule_with_violations

from hybrid_common import get_csp_seed
from hill_climbing.solver import run_hill_climbing_from_seed
from genetic.solver import run_genetic_from_seed


def _viol_counts(result):
    v = result.get("violations") or []
    hard = sum(1 for x in v if x.get("severity") == "hard")
    soft = sum(1 for x in v if x.get("severity") == "soft")
    return hard, soft, len(v)


def _summ(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return (None, None, None, None)
    mean = statistics.mean(vals)
    std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
    return (mean, std, min(vals), max(vals))


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print("Usage: python hybrid_experiment.py <seconds> <N>")
        print("Example: python hybrid_experiment.py 120 15")
        return 1
    try:
        seconds = float(argv[0])
        n_runs = int(argv[1])
    except ValueError:
        print("Args: <seconds> (number) <N> (integer). Example: 120 15")
        return 1
    if n_runs < 1:
        print("N must be >= 1.")
        return 1

    per_run = 3 if RUN_CHAIN else 2
    est_min = (n_runs * per_run * seconds) / 60.0
    print("=" * 64)
    print(f"HYBRID EXPERIMENT: {n_runs} runs @ {seconds}s   (chain={'on' if RUN_CHAIN else 'off'})")
    print(f"Rough time estimate: ~{est_min:.0f} minutes")
    print("=" * 64)

    print("Fetching data once...")
    data = fetch_all_data()
    lookups = _build_lookup_maps(data)
    timeslot_ids = [ts["id"] for ts in data["timeslots"]]

    # Approaches we track. (CSP seed is measured directly from get_csp_seed.)
    approaches = ["CSP_SEED", "HC_HYBRID", "GA_HYBRID"]
    if RUN_CHAIN:
        approaches.append("CHAIN_CSP_GA_HC")

    stats = {a: {"score": [], "hard": [], "soft": [], "runtime": [], "feasible": 0}
             for a in approaches}

    fieldnames = [
        "run_index", "time_budget_s", "approach", "score",
        "hard_violations", "soft_violations", "total_violations",
        "runtime_seconds", "status", "timestamp",
    ]
    file_exists = os.path.exists(RESULTS_FILE)
    f = open(RESULTS_FILE, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    if not file_exists:
        writer.writeheader()

    def record(run_index, approach, score, hard, soft, total, runtime, status, ts):
        stats[approach]["score"].append(score)
        stats[approach]["hard"].append(hard)
        stats[approach]["soft"].append(soft)
        stats[approach]["runtime"].append(runtime)
        if hard == 0:
            stats[approach]["feasible"] += 1
        writer.writerow({
            "run_index": run_index, "time_budget_s": seconds, "approach": approach,
            "score": score, "hard_violations": hard, "soft_violations": soft,
            "total_violations": total, "runtime_seconds": runtime,
            "status": status, "timestamp": ts,
        })

    try:
        for run_index in range(1, n_runs + 1):
            print(f"\n----- Run {run_index}/{n_runs} -----")
            ts_str = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # --- CSP seed (baseline) ---
            (seed_map, csp_result), csp_perf = measure_performance(get_csp_seed, data)
            if seed_map is None:
                print(f"  CSP INFEASIBLE (status={csp_result.get('status')}) - skipping run.")
                continue
            # Score the seed on the violations basis (same scorer the hybrids use).
            seed_score = _score_schedule(seed_map, data, lookups)
            s_hard, s_soft, s_total = _viol_counts(csp_result)
            record(run_index, "CSP_SEED", seed_score, s_hard, s_soft, s_total,
                   csp_perf["runtime_seconds"], csp_result.get("status"), ts_str)
            print(f"  CSP_SEED        score={seed_score:<10} hard={s_hard} soft={s_soft} "
                  f"runtime={csp_perf['runtime_seconds']:.2f}s")

            # --- HC-hybrid ---
            hc_res, hc_perf = measure_performance(
                run_hill_climbing_from_seed, data, seed_map, time_budget_seconds=seconds
            )
            h_hard, h_soft, h_total = _viol_counts(hc_res)
            record(run_index, "HC_HYBRID", hc_res["score"], h_hard, h_soft, h_total,
                   hc_perf["runtime_seconds"], hc_res.get("status"), ts_str)
            print(f"  HC_HYBRID       score={hc_res['score']:<10} hard={h_hard} soft={h_soft} "
                  f"runtime={hc_perf['runtime_seconds']:.2f}s")

            # --- GA-hybrid ---
            ga_res, ga_perf = measure_performance(
                run_genetic_from_seed, data, seed_map, time_budget_seconds=seconds,
                seed_fraction=SEED_FRACTION, seed_mutations=SEED_MUTATIONS
            )
            g_hard, g_soft, g_total = _viol_counts(ga_res)
            record(run_index, "GA_HYBRID", ga_res["score"], g_hard, g_soft, g_total,
                   ga_perf["runtime_seconds"], ga_res.get("status"), ts_str)
            print(f"  GA_HYBRID       score={ga_res['score']:<10} hard={g_hard} soft={g_soft} "
                  f"runtime={ga_perf['runtime_seconds']:.2f}s")

            # --- CHAIN: take GA-hybrid's schedule, polish with HC ---
            if RUN_CHAIN:
                # Rebuild GA's best schedule map from its schedule_entries, then
                # feed it to HC as a seed for a final local-optimisation pass.
                ga_map = {ta["id"]: [] for ta in data["teacher_assignments"]}
                for e in ga_res["schedule_entries"]:
                    if e["tea_assignment_id"] in ga_map:
                        ga_map[e["tea_assignment_id"]].append(e["timeslot_id"])
                chain_res, chain_perf = measure_performance(
                    run_hill_climbing_from_seed, data, ga_map, time_budget_seconds=seconds
                )
                c_hard, c_soft, c_total = _viol_counts(chain_res)
                record(run_index, "CHAIN_CSP_GA_HC", chain_res["score"],
                       c_hard, c_soft, c_total, chain_perf["runtime_seconds"],
                       chain_res.get("status"), ts_str)
                print(f"  CHAIN(CSP>GA>HC) score={chain_res['score']:<10} hard={c_hard} "
                      f"soft={c_soft} runtime={chain_perf['runtime_seconds']:.2f}s")

            f.flush()  # persist after each run so a crash loses nothing
    finally:
        f.close()

    # ---- Summary ----
    print("\n" + "=" * 64)
    print(f"SUMMARY over {n_runs} runs @ {seconds}s")
    print("=" * 64)
    for a in approaches:
        s = stats[a]
        if not s["score"]:
            continue
        sc = _summ(s["score"]); h = _summ(s["hard"]); so = _summ(s["soft"]); rt = _summ(s["runtime"])
        n_done = len(s["score"])
        print(f"\n{a}")
        print(f"  score:   mean={sc[0]:.1f}  std={sc[1]:.1f}  min={sc[2]:.1f}  max={sc[3]:.1f}")
        print(f"  hard:    mean={h[0]:.2f}  std={h[1]:.2f}  min={h[2]}  max={h[3]}")
        print(f"  soft:    mean={so[0]:.2f}  std={so[1]:.2f}  min={so[2]}  max={so[3]}")
        print(f"  runtime: mean={rt[0]:.2f}s  std={rt[1]:.2f}s")
        print(f"  feasible (0 hard): {s['feasible']}/{n_done}  ({100*s['feasible']/n_done:.0f}%)")

    print(f"\nAll runs appended to {RESULTS_FILE}")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
