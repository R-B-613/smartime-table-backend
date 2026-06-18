"""
performance.py

A single, reusable wrapper for measuring runtime + peak memory of ANY
scheduling algorithm function (CSP, Genetic Algorithm, Hill Climbing -
they all get measured the exact same way, with no duplicated timing code
inside each algorithm's own file).

Uses the built-in `resource` module, which reports the WHOLE PROCESS's
peak memory (not just memory allocated by pure Python code). This matters
because CSP relies on OR-Tools' C++ engine internally - a Python-only
memory tool (like tracemalloc) would not "see" that C++ memory use, which
would make CSP look artificially lighter than it really is. `resource`
sees everything, so all 3 algorithms are compared fairly on the same scale.

Usage:
    from performance import measure_performance
    from csp.solver import run_csp

    result, metrics = measure_performance(run_csp, data)
    print(metrics)
    # {'runtime_seconds': 1.23, 'peak_memory_mb': 87.4}
"""

import time
import resource


def measure_performance(algorithm_function, *args, **kwargs):
    """
    Runs algorithm_function(*args, **kwargs), measuring wall-clock runtime
    and the process's peak memory usage during/after the call.

    Returns
    -------
    (result, metrics) tuple, where:
        result  = whatever algorithm_function returned (unchanged)
        metrics = {
            "runtime_seconds": float,
            "peak_memory_mb": float,   # peak RSS for the whole process so far
        }

    NOTE on peak_memory_mb: `resource.getrusage(...).ru_maxrss` reports the
    process's peak memory usage SINCE THE PROCESS STARTED, not just during
    this function call. For a script like main.py that runs one algorithm
    and exits, this is fine and accurate. If you ever call this multiple
    times in the SAME process (e.g. running all 3 algorithms back-to-back
    in one main.py run), the peak will keep climbing/staying high from
    previous calls rather than resetting - worth keeping in mind when
    comparing numbers across algorithms run in the same process.
    """
    start_time = time.perf_counter()

    result = algorithm_function(*args, **kwargs)

    end_time = time.perf_counter()
    runtime_seconds = end_time - start_time

    # ru_maxrss is in kilobytes on Linux, bytes on macOS - this project
    # runs on a Linux server, so we divide by 1024 to get MB.
    peak_memory_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_memory_mb = peak_memory_kb / 1024

    metrics = {
        "runtime_seconds": round(runtime_seconds, 4),
        "peak_memory_mb": round(peak_memory_mb, 2),
    }

    return result, metrics