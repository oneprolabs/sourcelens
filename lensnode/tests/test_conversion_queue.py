import threading
import time

from lensnode.conversion_queue import ConversionJob
from lensnode.conversion_queue import conversion_queue_from_context


def test_parallel_conversion_queue_honors_worker_limit():
    jobs = [ConversionJob(index, 4, index, None) for index in range(4)]
    lock = threading.Lock()
    active = 0
    peak = 0

    def handler(job):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return job.index

    queue = conversion_queue_from_context(
        {"conversion": {"workers": 2}}
    )
    results = list(queue.run(jobs, handler))

    assert queue.name == "parallel"
    assert peak == 2
    assert {item[1] for item in results} == {0, 1, 2, 3}


def test_parallel_conversion_queue_contains_handler_errors_per_job():
    jobs = [ConversionJob(index, 2, index, None) for index in range(2)]

    def handler(job):
        if job.index == 0:
            raise ValueError("broken document")
        return "ok"

    results = list(conversion_queue_from_context({}).run(jobs, handler))
    by_index = {job.index: result for job, result in results}

    assert isinstance(by_index[0], ValueError)
    assert by_index[1] == "ok"


def test_parallel_conversion_queue_caps_workers():
    queue = conversion_queue_from_context(
        {"conversion": {"workers": 100}}
    )

    assert queue.workers == 16
