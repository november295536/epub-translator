"""Run records, review round 3: each test reproduces one finding (it failed
before the fix). All text is invented."""

import os
import threading

from src.core.events import RunEvents, events_path
from tests.test_run_records import PROJECT, _make_project, _of, _records, _wait


def _tm(monkeypatch):
    from src.web import tasks as tasks_module
    from src.web.tasks import TaskManager
    TaskManager._instance = None
    monkeypatch.setattr(tasks_module, 'COORDINATOR_TICK_SECONDS', 0.02)
    return TaskManager()


def _reset():
    from src.web.tasks import TaskManager
    TaskManager._instance = None


class GatedEvent(threading.Event):
    """The task's pause gate; its `nth` wait() from a worker first blocks
    until `release` is set: a worker between its stop check and the gate."""

    def __init__(self, nth):
        super().__init__()
        self.nth, self.waits = nth, 0
        self.reached, self.release = threading.Event(), threading.Event()

    def wait(self, timeout=None):
        if threading.current_thread().name.startswith('Thread') and not self.reached.is_set():
            self.waits += 1
            if self.waits == self.nth:
                self.reached.set()
                assert self.release.wait(20)
        return super().wait(timeout)


def test_pause_after_a_streak_stop_cannot_hang_the_run(tmp_path, monkeypatch):
    """Five unexpected failures stop the run; a worker is between its stop
    check and the pause gate; a user pause arrives then. Before: accepted,
    the gate closed, the worker waited for ever and the run never ended."""
    from dataclasses import field
    from src.llm.fake import FakeBackend
    from src.web import tasks as tasks_module
    from tests.conftest import use_fake_backend
    tm = _tm(monkeypatch)
    _make_project(tmp_path, n=7)
    gate = GatedEvent(nth=6)  # the wait before the sixth segment

    original = tasks_module.TranslationTask.__init__

    def init(self, *a, **k):
        original(self, *a, **k)
        gate.set()  # as the task's own gate starts (open)
        self.pause_event = gate
    monkeypatch.setattr(tasks_module.TranslationTask, '__init__', init)

    def boom(request):
        raise KeyError('invented')
    try:
        with use_fake_backend(FakeBackend(responder=boom)):
            task = tm.start_translation(PROJECT, str(tmp_path), num_workers=1)
            assert gate.reached.wait(20)
            _wait(lambda: task.stop_event.is_set())  # the streak stopped the run
            paused = tm.pause_translation(PROJECT)
            gate.release.set()
            task._coordinator.join(10)
            alive = task._coordinator.is_alive()
            if alive:  # free the worker, so the test leaves no thread behind
                tm.stop_translation(PROJECT)
                task._coordinator.join(20)
    finally:
        _reset()
    assert paused is False and not alive
    assert task.state == 'failed'


def _run(tm, tmp_path, responder, n=1, workers=1):
    from src.llm.fake import FakeBackend
    from tests.conftest import use_fake_backend
    path = _make_project(tmp_path, n=n)
    with use_fake_backend(FakeBackend(responder=responder)):
        task = tm.start_translation(PROJECT, str(tmp_path), num_workers=workers)
        _wait(lambda: task.state in ('completed', 'stopped', 'failed') and not task.threads_alive())
    _wait(lambda: _of(_records(path), 'run_end'))
    return task, path


class TestStopWhileFinishing:
    def test_stop_after_the_last_result(self, tmp_path, monkeypatch):
        from tests.conftest import like
        tm = _tm(monkeypatch)
        done = tm._workers_done

        def workers_done(task):
            if done(task):
                tm.stop_translation(PROJECT)  # forced: right after the last result
                return True
            return False
        monkeypatch.setattr(tm, '_workers_done', workers_done)
        try:
            task, path = _run(tm, tmp_path, lambda r: like(r, '好'))
        finally:
            _reset()
        [end] = _of(_records(path), 'run_end')
        assert (task.state, end['state'], end['translated']) == ('completed', 'completed', 1)

    def test_stop_during_a_failure_drain(self, tmp_path, monkeypatch):
        from src.core import runcontrol
        from src.web import tasks as tasks_module
        from tests.conftest import like
        tm = _tm(monkeypatch)
        states = []
        flush = runcontrol.DebouncedSaver.flush

        def flush_and_stop(self):
            task = tm.tasks.get(PROJECT)
            if task is not None and task.finishing and not states:
                tm.stop_translation(PROJECT)  # forced: a stop in the drain
                states.append(task.state)
            return flush(self)
        monkeypatch.setattr(runcontrol.DebouncedSaver, 'flush', flush_and_stop)

        def explode(self, task, pm, res):
            raise ZeroDivisionError('invented')
        monkeypatch.setattr(tasks_module.TaskManager, '_apply_result', explode)
        try:
            task, path = _run(tm, tmp_path, lambda r: like(r, '好'))
        finally:
            _reset()
        assert states == ['running'] and task.state == 'failed'
        assert _of(_records(path), 'run_end')[0]['state'] == 'failed'
