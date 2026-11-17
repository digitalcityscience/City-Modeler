# Blender Plugin: 3DCityDB / CityGML - Importer / Exporter
# Year: 2026
# common/background_job.py
"""Long jobs without freezing Blender (T08; T09 uses the same runner).

A :class:`Job` has a worker function that produces plain data in a thread and
must not touch bpy. It hands results to the main thread with
:meth:`Job.post`; a modal timer operator drains those messages with a time
budget per tick (``TICK_BUDGET_S``), shows progress (``wm.progress_*`` and
the status bar) and cancels on Esc. Only one job runs at a time.

``bpy.app.background`` has no event loop: :func:`start` then runs the job
synchronously, so headless tests see the same result. :class:`JobDriver`
exposes the tick loop for benchmarks.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
from typing import Any, Callable, Dict, Optional

import bpy

TICK_INTERVAL_S = 0.05
TICK_BUDGET_S = 0.02

_current: Optional["JobDriver"] = None


class JobCancelled(Exception):
    pass


class Job:
    """Worker + main-thread callbacks.

    ``worker(job)`` runs in a thread (no bpy), may call ``job.post(msg)``,
    ``job.set_progress(done, total, text)`` and should stop when
    ``job.cancelled()``. ``on_message(msg)``, ``on_tick()`` (once per tick,
    before the messages; returns True after heavy work so the tick drains
    nothing more) and ``on_done(result, error, cancelled)`` run on the main
    thread.
    """

    def __init__(
        self,
        name: str,
        worker: Callable[["Job"], Any],
        *,
        on_message: Optional[Callable[[Any], None]] = None,
        on_done: Optional[Callable[[Any, Optional[BaseException], bool], None]] = None,
        on_tick: Optional[Callable[[], None]] = None,
        label: str = "",
    ):
        self.name = name
        self.label = label or name
        self.worker = worker
        self.on_message = on_message
        self.on_done = on_done
        self.on_tick = on_tick
        self._cancel = threading.Event()
        self._messages: "queue.SimpleQueue[Any]" = queue.SimpleQueue()
        self._progress = (0, 0, "")
        self._lock = threading.Lock()

    # worker side
    def post(self, msg: Any) -> None:
        self._messages.put(msg)

    def set_progress(self, done: int, total: int, text: str = "") -> None:
        with self._lock:
            self._progress = (done, total, text)

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # main-thread side
    def cancel(self) -> None:
        self._cancel.set()

    @property
    def progress(self):
        with self._lock:
            return self._progress


class JobDriver:
    """Runs one :class:`Job`: worker thread + :meth:`tick` on the main thread."""

    def __init__(self, job: Job):
        self.job = job
        self.result: Any = None
        self.error: Optional[BaseException] = None
        self.finished = False
        self.worker_done = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.stats: Dict[str, Any] = {"ticks": 0, "max_tick_s": 0.0, "max_gap_s": 0.0}
        self._last_tick_end: Optional[float] = None
        self.t0 = time.perf_counter()

    def _work(self):
        try:
            self.result = self.job.worker(self.job)
        except JobCancelled:
            self.job.cancel()
        except BaseException as exc:  # handed to on_done
            self.error = exc
            traceback.print_exc()
        finally:
            self.worker_done.set()

    def start_thread(self) -> None:
        self.thread = threading.Thread(target=self._work, name=f"cgml3-{self.job.name}", daemon=True)
        self.thread.start()

    def run_in_this_thread(self) -> None:
        self._work()

    def tick(self, budget_s: float = TICK_BUDGET_S) -> bool:
        """Drain messages for at most ``budget_s``; True once the job is finished."""
        if self.finished:
            return True
        t_start = time.perf_counter()
        if self._last_tick_end is not None:
            gap = t_start - self._last_tick_end
            if gap > self.stats["max_gap_s"]:
                self.stats["max_gap_s"] = gap
                self.stats["max_gap_at_s"] = round(t_start - self.t0, 3)
        worker_done = self.worker_done.is_set()
        deadline = t_start + budget_s
        drained = True
        # on_tick first: when it did heavy work (returned True) this tick does
        # not drain messages too, so the two never add up to one long block
        busy = False
        if self.job.on_tick is not None:
            try:
                busy = bool(self.job.on_tick())
            except Exception:
                traceback.print_exc()
        if busy and not worker_done:
            deadline = t_start
        while True:
            if not worker_done and time.perf_counter() >= deadline:
                drained = False
                break
            try:
                msg = self.job._messages.get_nowait()
            except queue.Empty:
                break
            if self.job.on_message is not None:
                try:
                    self.job.on_message(msg)
                except Exception:
                    traceback.print_exc()
        done = worker_done and drained
        if done:
            self._finish()
        t_end = time.perf_counter()
        self.stats["ticks"] += 1
        self.stats["max_tick_s"] = max(self.stats["max_tick_s"], t_end - t_start)
        self._last_tick_end = t_end
        return done

    def _finish(self) -> None:
        self.finished = True
        self.stats["seconds"] = time.perf_counter() - self.t0
        if self.job.on_done is not None:
            try:
                self.job.on_done(self.result, self.error, self.job.cancelled())
            except Exception:
                traceback.print_exc()


def current() -> Optional[JobDriver]:
    return _current if (_current is not None and not _current.finished) else None


def is_running() -> bool:
    return current() is not None


def cancel_current() -> bool:
    drv = current()
    if drv is None:
        return False
    drv.job.cancel()
    return True


def start(job: Job, context=None, *, manual: bool = False) -> Optional[JobDriver]:
    """Start ``job``; None when another job is running.

    In the UI a modal timer operator ticks the job; in background mode it
    runs synchronously before this returns. ``manual`` starts the worker
    thread and leaves the ticking to the caller (benchmarks).
    """
    global _current
    if is_running():
        return None
    drv = JobDriver(job)
    _current = drv
    if manual:
        drv.start_thread()
        return drv
    if bpy.app.background:
        drv.run_in_this_thread()
        while not drv.tick(budget_s=1e9):
            pass
        return drv
    drv.start_thread()
    try:
        res = bpy.ops.cgml3.background_job('INVOKE_DEFAULT')
    except RuntimeError:
        res = {'CANCELLED'}
    if 'RUNNING_MODAL' not in res:
        # no window to host the modal operator: plain timer, no Esc
        def _poll():
            return None if drv.tick() else TICK_INTERVAL_S
        bpy.app.timers.register(_poll, first_interval=TICK_INTERVAL_S)
    return drv


def _status_text(drv: JobDriver) -> str:
    done, total, text = drv.job.progress
    pct = f" {100.0 * done / total:.0f}%" if total else ""
    return f"{drv.job.label}{pct}{(' · ' + text) if text else ''} · Esc to cancel"


class CGML3_OT_background_job(bpy.types.Operator):
    """Progress and Esc handling of the running background job"""
    bl_idname = "cgml3.background_job"
    bl_label = "CityGML background job"
    bl_options = {'INTERNAL'}

    _timer = None
    _last_tick = 0.0

    def invoke(self, context, event):
        drv = current()
        if drv is None or context.window is None:
            return {'CANCELLED'}
        wm = context.window_manager
        self._timer = wm.event_timer_add(TICK_INTERVAL_S, window=context.window)
        wm.modal_handler_add(self)
        wm.progress_begin(0, 100)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        try:
            wm.progress_end()
        except Exception:
            pass
        try:
            context.workspace.status_text_set(None)
        except Exception:
            pass

    def modal(self, context, event):
        drv = _current
        if drv is None or drv.finished:
            self._end(context)
            return {'FINISHED'}
        if event.type == 'ESC' and event.value == 'PRESS':
            drv.job.cancel()
            try:
                context.workspace.status_text_set(f"{drv.job.label} · cancelling")
            except Exception:
                pass
            return {'RUNNING_MODAL'}
        # Event has no reference to its timer: any TIMER event may tick, at most
        # once per interval (other add-ons' timers must not speed it up).
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        now = time.perf_counter()
        if now - self._last_tick < TICK_INTERVAL_S * 0.8:
            return {'PASS_THROUGH'}
        self._last_tick = now
        finished = drv.tick()
        done, total, _ = drv.job.progress
        if total:
            context.window_manager.progress_update(min(100.0, 100.0 * done / total))
        try:
            context.workspace.status_text_set(_status_text(drv))
        except Exception:
            pass
        if finished:
            self._end(context)
            tag_redraw_all()
            return {'FINISHED'}
        return {'PASS_THROUGH'}

    def cancel(self, context):
        # Blender dropped the operator (window closed, file loaded): stop the
        # job and let a plain timer finish it, so the next job can start.
        self._end(context)
        drv = _current
        if drv is not None and not drv.finished:
            drv.job.cancel()

            def _poll():
                return None if drv.tick() else TICK_INTERVAL_S
            bpy.app.timers.register(_poll, first_interval=TICK_INTERVAL_S)


def tag_redraw_all() -> None:
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "PROPERTIES"}:
                area.tag_redraw()


classes = (CGML3_OT_background_job,)
