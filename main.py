"""
main.py

Orchestrator. Fetches data once from the DB, then runs each scheduling
algorithm on the SAME data, and (eventually) hands the results to a
comparator that picks the best one.

Currently only CSP is implemented - GA and Hill Climbing are added the
same way (each gets its own folder + run_xxx(data) function), then their
results get added to the `results` list below.
"""

from data_access import fetch_all_data
from csp.solver import run_csp
from hill_climbing.solver import run_hill_climbing
from genetic.solver import run_genetic
from performance import measure_performance
from comparator import save_and_select_best_result


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

    print("Saving all results and selecting the best one...")
    comparison = save_and_select_best_result(results)

    if comparison["best_run_id"] is not None:
        print(f"All runs saved: {comparison['saved_run_ids']}")
        print(f"Best result: {comparison['best_algorithm']} "
              f"(run_id={comparison['best_run_id']}, score={comparison['best_score']})")
    else:
        print("No valid schedule was found in any algorithm - nothing was saved.")


if __name__ == "__main__":
    main()