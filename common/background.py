# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# common/background.py
"""Run a bpy-free function in a worker thread and hand the result back on the main thread.

Small by design: one thread per job, a timer polls for completion and calls
``on_done(result, error)`` where bpy may be used again. The worker function
must not touch bpy. The general import pipeline (T04) builds on the same idea.
"""

from __future__ import annotations

import threading
import traceback
from typing import Any, Callable, Dict, Optional

import bpy

POLL_INTERVAL_S = 0.1

_jobs: Dict[str, "_Job"] = {}


class _Job:
    def __init__(self, name: str, fn: Callable[[], Any], on_done: Callable[[Any, Optional[BaseException]], None]):
        self.name = name
        self.fn = fn
        self.on_done = on_done
        self.result: Any = None
        self.error: Optional[BaseException] = None
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._work, name=f"cgml3-{name}", daemon=True)

    def _work(self):
        try:
            self.result = self.fn()
        except BaseException as exc:  # handed to on_done, never raised in the thread
            self.error = exc
        finally:
            self.done.set()


def is_running(name: str) -> bool:
    job = _jobs.get(name)
    return job is not None and not job.done.is_set()


def run(name: str, fn: Callable[[], Any], on_done: Callable[[Any, Optional[BaseException]], None]) -> bool:
    """Start ``fn`` in a thread; False if a job with this name is still running."""
    if is_running(name):
        return False
    job = _Job(name, fn, on_done)
    _jobs[name] = job
    job.thread.start()

    def _poll():
        if not job.done.is_set():
            return POLL_INTERVAL_S
        if _jobs.get(name) is job:
            del _jobs[name]
        try:
            job.on_done(job.result, job.error)
        except Exception:
            traceback.print_exc()
        tag_redraw()
        return None

    bpy.app.timers.register(_poll, first_interval=POLL_INTERVAL_S)
    return True


def wait(name: str, timeout: Optional[float] = None) -> bool:
    """Block until a job finished (headless scripts and tests only)."""
    job = _jobs.get(name)
    if job is None:
        return True
    return job.done.wait(timeout)


def tag_redraw() -> None:
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "PREFERENCES"}:
                area.tag_redraw()
