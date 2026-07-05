"""
diagnose_violations.py

Standalone, read-only diagnostic. Run it once to see EXACTLY what is
costing the current (is_selected) schedule its score - the answer to
"why is my score 20,070?".

It:
  1. loads all data (data_access.fetch_all_data)
  2. finds the current selected run
  3. rebuilds that run's schedule from the saved `schedule` rows
  4. scores it with scoring_violations, printing every violation + penalty

Run from the repo root:
    python diagnose_violations.py

Changes nothing in the database.
"""

from data_access import fetch_all_data, get_db_connection
from genetic.solver import _build_lookup_maps
from scoring_violations import score_genetic_schedule_with_violations


def _current_run_id():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, algorithm, score FROM schedule_runs "
                "WHERE is_selected = true ORDER BY run_at DESC, id DESC LIMIT 1;"
            )
            return cur.fetchone()
    finally:
        conn.close()


def _schedule_for_run(run_id, teacher_assignments):
    # start every assignment with an empty list so scoring never KeyErrors
    schedule = {ta["id"]: [] for ta in teacher_assignments}
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tea_assignment_id, timeslot_id FROM schedule WHERE run_id = %s;",
                (run_id,),
            )
            for a_id, t_id in cur.fetchall():
                if a_id in schedule:
                    schedule[a_id].append(t_id)
    finally:
        conn.close()
    return schedule


def main():
    row = _current_run_id()
    if row is None:
        print("No selected run found. Generate + publish a schedule first.")
        return
    run_id, algorithm, score = row
    print(f"Current run: id={run_id}  algorithm={algorithm}  saved score={score}")

    data = fetch_all_data()
    lookups = _build_lookup_maps(data)
    schedule = _schedule_for_run(run_id, data["teacher_assignments"])

    total, violations = score_genetic_schedule_with_violations(schedule, data, lookups)

    print(f"\nRecomputed total penalty: {total}")
    print(f"Violations found: {len(violations)}\n")
    print(f"{'PENALTY':>10}  {'SEVERITY':<6}  DETAIL")
    print("-" * 80)
    for v in violations:
        # Hebrew prints fine in a UTF-8 terminal; if it looks garbled that's
        # just the terminal, the data is correct.
        print(f"{v['penalty']:>10}  {v['severity']:<6}  {v['detail']}")
    print("-" * 80)

    hard = sum(v["penalty"] for v in violations if v["severity"] == "hard")
    soft = sum(v["penalty"] for v in violations if v["severity"] == "soft")
    print(f"Hard total: {hard}   Soft total: {soft}   Grand total: {total}")


if __name__ == "__main__":
    main()
