"""
main.py

Orchestrator. Fetches data once from the DB, then runs each scheduling
algorithm on the SAME data, and (eventually) hands the results to a
comparator that picks the best one.

Currently only CSP is implemented - GA and Hill Climbing are added the
same way (each gets its own folder + run_xxx(data) function), then their
results get added to the `results` list below.
"""

from data_access import fetch_all_data, save_schedule_run
from csp.solver import run_csp
from hill_climbing.solver import run_hill_climbing
from genetic.solver import run_genetic
from performance import measure_performance

# TODO: once written, import this the same way:
# from comparator import pick_best_result


def main():
    print("Fetching data from the database...")
    data = fetch_all_data()

    print("Running CSP solver...")
    csp_result, csp_metrics = measure_performance(run_csp, data)
    print(f"CSP finished with status={csp_result['status']}, score={csp_result['score']}")
    print(f"CSP performance: runtime={csp_metrics['runtime_seconds']}s, "
          f"peak_memory={csp_metrics['peak_memory_mb']}MB")

    results = [csp_result]

    print("Running Hill Climbing solver...")
    hc_result, hc_metrics = measure_performance(run_hill_climbing, data)
    print(f"Hill Climbing finished with status={hc_result['status']}, score={hc_result['score']}")
    print(f"Hill Climbing performance: runtime={hc_metrics['runtime_seconds']}s, "
          f"peak_memory={hc_metrics['peak_memory_mb']}MB")

    results.append(hc_result)

    print("Running Genetic Algorithm solver...")
    genetic_result, genetic_metrics = measure_performance(run_genetic, data)
    print(f"Genetic Algorithm finished with status={genetic_result['status']}, score={genetic_result['score']}")
    print(f"Genetic Algorithm performance: runtime={genetic_metrics['runtime_seconds']}s, "
          f"peak_memory={genetic_metrics['peak_memory_mb']}MB")

    results.append(genetic_result)

    # TODO: once implemented, replace the manual min() selection below with:
    # best_result = pick_best_result(results)

    # For now (no comparator yet), just pick the lowest score among
    # whatever results we have (manually doing what pick_best_result will
    # do automatically once it exists).
    valid_results = [r for r in results if r["score"] is not None]
    best_result = min(valid_results, key=lambda r: r["score"]) if valid_results else None

    if best_result is not None and best_result["score"] is not None:
        print(f"Saving best result ({best_result['algorithm']}) to the database...")
        run_id = save_schedule_run(
            algorithm=best_result["algorithm"],
            score=best_result["score"],
            schedule_entries=best_result["schedule_entries"],
        )
        print(f"Saved as schedule_runs.id = {run_id}")
    else:
        print("No valid schedule was found - nothing was saved.")


if __name__ == "__main__":
    main()