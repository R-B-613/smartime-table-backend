"""
view_schedule.py

Quick, curiosity-driven visualization of a saved schedule run - prints a
day x hour grid table directly in the terminal (no extra setup needed).

Usage examples (run from inside smartime-table-backend/, venv activated):

    python view_schedule.py --run 4 --group "כיתה א1"
    python view_schedule.py --run 4 --teacher "אחת"
    python view_schedule.py --run 4 --list-groups
    python view_schedule.py --run 4 --list-teachers

This is a throwaway/diagnostic tool, not part of the main pipeline - it's
just for visually sanity-checking a schedule, not used by main.py.
"""

import argparse
from data_access import get_db_connection
from psycopg2.extras import RealDictCursor

DAY_NAMES = {1: "א'", 2: "ב'", 3: "ג'", 4: "ד'", 5: "ה'", 6: "ו'"}


def fetch_schedule_rows(run_id, group_name=None, teacher_last_name=None):
    """
    Fetches all schedule rows for a given run, optionally filtered to one
    student group or one teacher (matched by last_name, since your fake
    data has unique-ish last names like 'שתיים', 'שלוש', etc.)
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = """
                SELECT
                    t.day_of_week,
                    t.hour_of_day,
                    te.first_name,
                    te.last_name,
                    sub.subject_name,
                    sg.group_name,
                    r.room_name
                FROM schedule s
                JOIN timeslots t ON s.timeslot_id = t.id
                JOIN teacher_assignments ta ON s.tea_assignment_id = ta.id
                JOIN teachers te ON ta.teacher_id = te.id
                JOIN curriculum_requirements cr ON ta.cur_requirement_id = cr.id
                JOIN subjects sub ON cr.subject_id = sub.id
                JOIN student_groups sg ON cr.student_group_id = sg.id
                LEFT JOIN rooms r ON s.room_id = r.id
                WHERE s.run_id = %s
            """
            params = [run_id]

            if group_name:
                query += " AND sg.group_name = %s"
                params.append(group_name)

            if teacher_last_name:
                query += " AND te.last_name = %s"
                params.append(teacher_last_name)

            query += " ORDER BY t.day_of_week, t.hour_of_day;"

            cursor.execute(query, params)
            return cursor.fetchall()
    finally:
        conn.close()


def fetch_distinct_groups(run_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT sg.group_name
                FROM schedule s
                JOIN teacher_assignments ta ON s.tea_assignment_id = ta.id
                JOIN curriculum_requirements cr ON ta.cur_requirement_id = cr.id
                JOIN student_groups sg ON cr.student_group_id = sg.id
                WHERE s.run_id = %s
                ORDER BY sg.group_name;
            """, (run_id,))
            return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def fetch_distinct_teachers(run_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT te.first_name, te.last_name
                FROM schedule s
                JOIN teacher_assignments ta ON s.tea_assignment_id = ta.id
                JOIN teachers te ON ta.teacher_id = te.id
                WHERE s.run_id = %s
                ORDER BY te.last_name;
            """, (run_id,))
            return cursor.fetchall()
    finally:
        conn.close()


def print_grid(rows, title):
    """
    Prints a day (columns) x hour (rows) grid. Each cell shows whatever
    lesson(s) fall there - usually one, but printed as a list in case of
    unexpected overlaps (which would itself be useful to notice).
    """
    if not rows:
        print(f"\n{title}\n(No scheduled lessons found for this filter/run.)\n")
        return

    # Build a lookup: (day, hour) -> list of "label" strings
    grid = {}
    max_hour = 1
    for row in rows:
        day = row["day_of_week"]
        hour = row["hour_of_day"]
        max_hour = max(max_hour, hour)

        label_parts = []
        if row.get("subject_name"):
            label_parts.append(row["subject_name"])
        if row.get("group_name"):
            label_parts.append(row["group_name"])
        if row.get("first_name") and row.get("last_name"):
            label_parts.append(f"{row['first_name']} {row['last_name']}")
        if row.get("room_name"):
            label_parts.append(f"[{row['room_name']}]")

        label = " | ".join(label_parts)
        grid.setdefault((day, hour), []).append(label)

    days_present = sorted(set(row["day_of_week"] for row in rows))
    col_width = 28

    print(f"\n{title}\n" + "=" * len(title))

    # Header row
    header = "hour".ljust(6)
    for day in days_present:
        header += DAY_NAMES.get(day, str(day)).center(col_width)
    print(header)
    print("-" * len(header))

    for hour in range(1, max_hour + 1):
        row_str = str(hour).ljust(6)
        for day in days_present:
            cell_labels = grid.get((day, hour), [])
            cell_text = " / ".join(cell_labels) if cell_labels else "-"
            if len(cell_text) > col_width - 1:
                cell_text = cell_text[:col_width - 4] + "..."
            row_str += cell_text.center(col_width)
        print(row_str)

    print()


def main():
    parser = argparse.ArgumentParser(description="Visualize a saved schedule run as a grid.")
    parser.add_argument("--run", type=int, required=True, help="schedule_runs.id to view")
    parser.add_argument("--group", type=str, help="Filter to one student group (exact group_name)")
    parser.add_argument("--teacher", type=str, help="Filter to one teacher (exact last_name)")
    parser.add_argument("--list-groups", action="store_true", help="List all group names in this run")
    parser.add_argument("--list-teachers", action="store_true", help="List all teachers in this run")
    args = parser.parse_args()

    if args.list_groups:
        groups = fetch_distinct_groups(args.run)
        print("\nGroups in this run:")
        for g in groups:
            print(f"  - {g}")
        return

    if args.list_teachers:
        teachers = fetch_distinct_teachers(args.run)
        print("\nTeachers in this run:")
        for t in teachers:
            print(f"  - {t['first_name']} {t['last_name']}")
        return

    if args.group:
        rows = fetch_schedule_rows(args.run, group_name=args.group)
        print_grid(rows, f"Schedule for group: {args.group} (run {args.run})")
    elif args.teacher:
        rows = fetch_schedule_rows(args.run, teacher_last_name=args.teacher)
        print_grid(rows, f"Schedule for teacher (last name): {args.teacher} (run {args.run})")
    else:
        print("Please provide --group, --teacher, --list-groups, or --list-teachers.")


if __name__ == "__main__":
    main()