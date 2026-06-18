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

# TODO: once written, import these the same way:
# from genetic.solver import run_genetic
# from hill_climbing.solver import run_hill_climbing
# from comparator import pick_best_result


def main():
    print("Fetching data from the database...")
    data = fetch_all_data()

    print("Running CSP solver...")
    csp_result = run_csp(data)
    print(f"CSP finished with status={csp_result['status']}, score={csp_result['score']}")

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










































# import os
# import psycopg2
# from psycopg2.extras import RealDictCursor
# from ortools.sat.python import cp_model

# def get_db_credentials(filepath="~/credentials.txt"):
#     """
#     A function that reads the text file, searches for the database details, and returns them as a dictionary
#     """
#     expanded_path = os.path.expanduser(filepath)
    
#     creds = {
#         "host": "localhost",
#         "dbname": "",
#         "user": "",
#         "password": ""
#     }
    
#     try:
#         with open(expanded_path, 'r', encoding='utf-8') as f:
#             for line in f:
#                 line = line.strip()
#                 if line.startswith("Database Name:"):
#                     creds["dbname"] = line.split(":", 1)[1].strip()
#                 elif line.startswith("Database User:"):
#                     creds["user"] = line.split(":", 1)[1].strip()
#                 elif line.startswith("Database Password:"):
#                     creds["password"] = line.split(":", 1)[1].strip()
#                 elif line.startswith("Host:"):
#                     host_part = line.split(":", 1)[1].strip()
#                     creds["host"] = host_part.split()[0].strip()
                    
#         return creds
#     except Exception as e:
#         print(f"Error reading the credentials file: {e}")
#         return None

# def get_db_connection():
#     creds = get_db_credentials()
#     if not creds or not creds['dbname']:
#         raise ValueError("Failed to extract connection details from the file.")
        
#     return psycopg2.connect(
#         host=creds["host"],
#         database=creds["dbname"],
#         user=creds["user"],
#         password=creds["password"]
#     )

# def fetch_data():
#     conn = get_db_connection()
#     data = {}
#     try:
#         with conn.cursor(cursor_factory=RealDictCursor) as cursor:
#             cursor.execute("""
#                 SELECT id, day_of_week, hour_of_day 
#                 FROM timeslots 
#                 ORDER BY day_of_week, hour_of_day;
#             """)
#             data['timeslots'] = cursor.fetchall()

#             cursor.execute("""
#                 SELECT 
#                     cr.id as req_id, 
#                     cr.student_group_id, 
#                     cr.subject_id, 
#                     cr.weekly_hours,
#                     ta.teacher_id
#                 FROM curriculum_requirements cr
#                 JOIN teacher_assignments ta ON cr.id = ta.cur_requirement_id;
#             """)
#             data['requirements'] = cursor.fetchall()
            
#     finally:
#         conn.close()
        
#     return data

# def build_and_solve_schedule():
#     print("Connecting to the DB and fetching data...")
#     try:
#         data = fetch_data()
#     except Exception as e:
#         print(f"Error fetching data: {e}")
#         return
        
#     timeslots = data['timeslots']
#     requirements = data['requirements']
    
#     if not timeslots or not requirements:
#         print("Warning: Missing data in the database (timeslots or curriculum requirements tables are empty)")
#         return

#     model = cp_model.CpModel()
#     schedule_vars = {}
    
#     print("Building decision variables...")
#     for req in requirements:
#         for ts in timeslots:
#             var_name = f"req_{req['req_id']}_time_{ts['id']}"
#             schedule_vars[(req['req_id'], ts['id'])] = model.NewBoolVar(var_name)

#     print("Adding hard constraints...")
    
#     # 1. Completing the weekly hours quota for each requirement
#     for req in requirements:
#         model.Add(sum(schedule_vars[(req['req_id'], ts['id'])] for ts in timeslots) == req['weekly_hours'])
#         # Technical note: We used a direct equality equation inside Add
#         model.Add(sum(schedule_vars[(req['req_id'], ts['id'])] for ts in timeslots) == req['weekly_hours'])


#     # 2. No two subjects/teachers for a class at the same time
#     group_ids = set(req['student_group_id'] for req in requirements)
#     for group_id in group_ids:
#         group_reqs = [req for req in requirements if req['student_group_id'] == group_id]
#         for ts in timeslots:
#             model.AddAtMostOne(schedule_vars[(req['req_id'], ts['id'])] for req in group_reqs)

#     # 3. No teacher can teach two classes at the same time
#     teacher_ids = set(req['teacher_id'] for req in requirements)
#     for teacher_id in teacher_ids:
#         teacher_reqs = [req for req in requirements if req['teacher_id'] == teacher_id]
#         for ts in timeslots:
#             model.AddAtMostOne(schedule_vars[(req['req_id'], ts['id'])] for req in teacher_reqs)

#     print("Solving the model... (limited to 60 seconds)")
#     solver = cp_model.CpSolver()
#     solver.parameters.max_time_in_seconds = 60.0 
    
#     status = solver.Solve(model)

#     if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
#         print("\nFound a solution! Here's the schedule:")
#         count = 0
#         for req in requirements:
#             for ts in timeslots:
#                 if solver.Value(schedule_vars[(req['req_id'], ts['id'])]) == 1:
#                     print(f"-> group {req['student_group_id']} | teacher {req['teacher_id']} | subject {req['subject_id']} | day {ts['day_of_week']} hour {ts['hour_of_day']}")
#                     count += 1
#                     if count >= 15:
#                         print("...and more scheduled sessions.")
#                         return
#     elif status == cp_model.INFEASIBLE:
#         print("\nNo solution found. The constraints are too tight (Infeasible).")
#     else:
#         print("\n The algorithm stopped without reaching a conclusion (status: {}).".format(solver.StatusName(status)))

# if __name__ == "__main__":
#     build_and_solve_schedule()