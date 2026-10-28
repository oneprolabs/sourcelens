"""Event-loop responsiveness probes for the control-channel process.

The control channel stays alive because the asyncio loop answers the control
plane's WebSocket pings. Any work that keeps the loop from running long enough
for a pong to miss its budget tears the connection down, even when the host is
otherwise idle. This module measures how far the loop falls behind and records
where it is stuck, so a connection drop can be traced to the code that caused
it instead of being blamed on the network.
"""

import asyncio
import logging
import sys
import threading
import time
import traceback
from pathlib import Path

from .logging_utils import task_log

LOGGER = logging.getLogger("lensnode")

LOOP_LAG_SAMPLE_INTERVAL_S = 0.5
LOOP_LAG_WARN_THRESHOLD_S = 2.0
LOOP_LAG_WARN_COOLDOWN_S = 60.0
LOOP_LAG_STACK_FRAME_LIMIT = 12


def _format_frame(frame, limit):
    """Return stack lines for one frame, compacted for log details."""

    if frame is None:
        return ["<stack unavailable>"]
    lines = []
    for entry in traceback.format_stack(frame, limit=limit):
        lines.extend(line.rstrip() for line in entry.splitlines() if line.strip())
    return lines


def _format_thread(thread, frame):
    """Return a one-line innermost location for one worker thread."""

    if frame is None:
        return f"{thread.name}: <exited>"
    innermost = traceback.extract_stack(frame, limit=1)
    if not innermost:
        return f"{thread.name}: <unknown>"
    entry = innermost[-1]
    return f"{thread.name}: {Path(entry.filename).name}:{entry.lineno} in {entry.name}"


class LoopLagProbe:
    """Report event-loop lag and dump the loop stack while it is stalled.

    ``collect`` is called from the heartbeat path and returns the worst lag
    observed since the previous call, so a stall shorter than the heartbeat
    interval still shows up in the report. The watchdog thread notices that the
    loop stopped sampling and dumps stacks *during* the stall — after the loop
    resumes the blocking frame is gone.
    """

    def __init__(
        self,
        interval_s=LOOP_LAG_SAMPLE_INTERVAL_S,
        warn_threshold_s=LOOP_LAG_WARN_THRESHOLD_S,
        warn_cooldown_s=LOOP_LAG_WARN_COOLDOWN_S,
    ):
        self.interval_s = float(interval_s)
        self.warn_threshold_s = float(warn_threshold_s)
        self.warn_cooldown_s = float(warn_cooldown_s)
        self._max_lag_s = 0.0
        self._last_lag_s = 0.0
        self._stall_count = 0
        self._loop_thread_id = None
        self._loop_beat = 0
        self._beat_at = time.monotonic()
        self._dumped_beat = -1
        self._lag_logged_at = 0.0
        self._dump_logged_at = 0.0
        self._sampler_task = None
        self._watchdog_stop = threading.Event()
        self._watchdog = None

    def start(self):
        """Start sampling on the running loop and its stall watchdog."""

        if self._sampler_task is None or self._sampler_task.done():
            self._sampler_task = asyncio.create_task(self._sample())
        if self._watchdog is None:
            self._watchdog = threading.Thread(
                target=self._watch,
                name="lensnode-loop-watchdog",
                daemon=True,
            )
            self._watchdog.start()

    async def stop(self):
        """Stop sampling and the watchdog thread."""

        task = self._sampler_task
        self._sampler_task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._watchdog_stop.set()
        watchdog = self._watchdog
        self._watchdog = None
        if watchdog is not None:
            watchdog.join(timeout=1)

    def collect(self):
        """Return lag metrics since the previous call and reset the window."""

        metrics = {
            "loop_lag_ms": round(self._max_lag_s * 1000, 1),
            "loop_stall_count": self._stall_count,
        }
        # Keep the most recent sample as the new floor so an idle window
        # reports the current lag instead of dropping to zero.
        self._max_lag_s = self._last_lag_s
        self._stall_count = 0
        return metrics

    async def _sample(self):
        """Measure scheduling delay of a fixed-interval sleep."""

        loop = asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        expected = loop.time() + self.interval_s
        while True:
            await asyncio.sleep(self.interval_s)
            now = loop.time()
            self._record(max(0.0, now - expected))
            expected = now + self.interval_s

    def _record(self, lag_s):
        """Store one lag sample and warn when the loop recovered from a stall."""

        self._last_lag_s = lag_s
        self._max_lag_s = max(self._max_lag_s, lag_s)
        self._loop_beat += 1
        self._beat_at = time.monotonic()
        if lag_s < self.warn_threshold_s:
            return
        self._stall_count += 1
        monotonic = time.monotonic()
        if monotonic - self._lag_logged_at < self.warn_cooldown_s:
            return
        self._lag_logged_at = monotonic
        LOGGER.warning(
            task_log(
                ("LensNode event loop resumed after a stall."),
                details=[
                    f"LoopLagMs: {round(lag_s * 1000)}",
                    f"WarnThresholdMs: {round(self.warn_threshold_s * 1000)}",
                ],
            )
        )

    def _watch(self):
        """Dump stacks while the loop has stopped sampling."""

        poll_s = max(0.1, self.interval_s)
        while not self._watchdog_stop.wait(poll_s):
            if self._loop_thread_id is None:
                continue
            silence_s = time.monotonic() - self._beat_at
            if silence_s < self.warn_threshold_s:
                continue
            beat = self._loop_beat
            if beat == self._dumped_beat:
                continue
            monotonic = time.monotonic()
            if monotonic - self._dump_logged_at < self.warn_cooldown_s:
                continue
            self._dumped_beat = beat
            self._dump_logged_at = monotonic
            self._log_stall(silence_s)

    def _log_stall(self, silence_s):
        """Write the current loop and worker stacks for one stalled window."""

        frames = sys._current_frames()
        loop_frame = frames.get(self._loop_thread_id)
        details = [
            f"LoopSilentMs: {round(silence_s * 1000)}",
            f"WarnThresholdMs: {round(self.warn_threshold_s * 1000)}",
            "EventLoopStack:",
        ]
        details.extend(_format_frame(loop_frame, LOOP_LAG_STACK_FRAME_LIMIT))
        details.append("OtherThreadStacks:")
        for thread in threading.enumerate():
            if thread is self._watchdog or thread.ident is None:
                continue
            if thread.ident == self._loop_thread_id:
                continue
            details.append(_format_thread(thread, frames.get(thread.ident)))
        LOGGER.error(
            task_log(
                "Detected a stalled LensNode event loop.",
                details=details,
            )
        )
