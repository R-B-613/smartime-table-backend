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
from performance import measure_performance

# TODO: once written, import these the same way:
# from genetic.solver import run_genetic
# from hill_climbing.solver import run_hill_climbing
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

    # TODO: once implemented, add the other two the same way:
    # genetic_result = run_genetic(data)
    # results.append(genetic_result)
    #
    # hill_climbing_result = run_hill_climbing(data)
    # results.append(hill_climbing_result)
    #
    # best_result = pick_best_result(results)

    # For now (CSP only), just treat the single result as "the best" one.
    best_result = csp_result

    if best_result["score"] is not None:
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