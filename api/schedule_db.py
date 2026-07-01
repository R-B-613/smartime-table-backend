"""
api/schedule_db.py

Read-only queries for showing the CURRENT timetable - the most recent run
that was marked is_selected = true. Reuses the same table JOINs as your
view_schedule.py, so the data lines up with what you already verified in
the terminal.

Nothing here writes to the database.
"""

from psycopg2.extras import RealDictCursor

from data_access import get_db_connection


def get_current_run():
    """
    Returns the latest schedule_runs row with is_selected = true
    ({id, algorithm, score, run_at}), or None if no schedule has been
    generated yet.

    "Latest" because is_selected can be true on more than one run over time
    (each generation marks its own winner); the newest one is the current
    timetable.
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT id, algorithm, score, run_at
                FROM schedule_runs
                WHERE is_selected = true
                ORDER BY run_at DESC, id DESC
                LIMIT 1;
                """
            )
            return cursor.fetchone()
    finally:
        conn.close()


def get_schedule_entries(run_id: int, teacher_id: int = None):
    """
    All lessons for a run as flat rows:
        {day_of_week, hour_of_day, teacher_id,
         teacher_first_name, teacher_last_name,
         subject_name, group_name, room_name}

    If teacher_id is given, only that teacher's lessons are returned (used
    for the teacher's own read-only view).
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            query = """
                SELECT
                    t.day_of_week,
                    t.hour_of_day,
                    te.id            AS teacher_id,
                    te.first_name    AS teacher_first_name,
                    te.last_name     AS teacher_last_name,
                    sub.subject_name,
                    sg.group_name,
                    r.room_name
                FROM schedule s
                JOIN timeslots t             ON s.timeslot_id = t.id
                JOIN teacher_assignments ta  ON s.tea_assignment_id = ta.id
                JOIN teachers te             ON ta.teacher_id = te.id
                JOIN curriculum_requirements cr ON ta.cur_requirement_id = cr.id
                JOIN subjects sub            ON cr.subject_id = sub.id
                JOIN student_groups sg       ON cr.student_group_id = sg.id
                LEFT JOIN rooms r            ON s.room_id = r.id
                WHERE s.run_id = %s
            """
            params = [run_id]
            if teacher_id is not None:
                query += " AND te.id = %s"
                params.append(teacher_id)
            query += " ORDER BY t.day_of_week, t.hour_of_day;"

            cursor.execute(query, params)
            return cursor.fetchall()
    finally:
        conn.close()
