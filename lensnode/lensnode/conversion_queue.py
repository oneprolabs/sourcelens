from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass


@dataclass
class ConversionJob:
    """One file conversion job."""

    index: int
    total: int
    item: object
    path: object


class ConversionQueue:
    """Base conversion queue executor."""

    name = "base"

    def run(self, jobs, handler):
        """Run conversion jobs with a handler callable."""

        raise NotImplementedError


class InlineConversionQueue(ConversionQueue):
    """Synchronous in-process conversion queue."""

    name = "inline"

    def run(self, jobs, handler):
        """Run jobs in order and yield handler results."""

        for job in jobs:
            try:
                result = handler(job)
            except Exception as exc:
                result = exc
            yield job, result


class ParallelConversionQueue(ConversionQueue):
    """Run conversion jobs concurrently with a bounded worker pool."""

    name = "parallel"

    def __init__(self, workers=4):
        self.workers = min(16, max(1, int(workers or 1)))

    def run(self, jobs, handler):
        """Run jobs concurrently and yield results in completion order."""

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {executor.submit(handler, job): job for job in jobs}
            for future in as_completed(futures):
                job = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = exc
                yield job, result


def conversion_queue_from_context(context):
    """Return the conversion queue configured for this context."""

    queue_name = (
        (context.get("conversion") or {}).get("queue")
        or context.get("conversion_queue")
        or "parallel"
    )
    if queue_name == "parallel":
        return ParallelConversionQueue(
            (context.get("conversion") or {}).get("workers") or 4
        )
    return InlineConversionQueue()
