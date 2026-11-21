#!/usr/bin/env python3
"""Phase 1, review rounds 1 to 4: each fixed behaviour is guarded by a test.

Puts each old behaviour back, one at a time, into a copy of the committed
code (git archive HEAD, in a temporary directory removed afterwards) and
runs the tests that guard it. Every revert must make at least one of them
fail. Prints OK/MISS per revert and the number not detected.

Round 4 moved the code of 2, R3-1 and R3-9 (web), whose reverts follow it,
and adds the reverts "R4-<item>".

The cleanup after Phase 9 moved the reverts whose code Phases 2-7 rewrote
to the new text (the same old behaviour at the new place).

Usage:
    python3 scripts/verify/phase1_reverts.py [name-prefix ...]

Round 1 reverts whose code review round 2 moved put back the same old
behaviour at the new place (items 8, 9, 24); the round 2 reverts are named
"R2-<item>"; round 3 moved the code of 9, 10, R2-1, R2-5, R2-16 and
R2-20, whose reverts follow it, and adds the reverts "R3-<item>".

Exit code: 0 when every revert is detected, 1 otherwise.
"""
import os
import shutil
import subprocess
import sys
import tempfile

REPO = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True,
                      check=True, cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()
PY = sys.executable
only = sys.argv[1:]

R = []  # (name, [(file, old, new)], tests)


def r(name, edits, tests):
    R.append((name, edits, tests))


P, T, CLI, LLM, TR, WEB, REV = ('src/core/project.py', 'src/web/tasks.py', 'translate.py',
                                'src/llm/cli.py', 'src/core/translator.py',
                                'src/web/blueprints/review.py', 'src/web/blueprints/review.py')

r('1 mutex per manager, not per project',
  [(P, 'mutex = _project_mutexes.get(key)', 'mutex = None')],
  ['tests/test_project.py::TestSharedProjectMutex'])
r('1 temp file left after a failed save',
  [(P, '                try:\n                    os.remove(tmp_file)\n                except OSError:\n                    pass\n',
    '')],
  ['tests/test_project.py::TestAtomicSave'])
r('2 re-translate applies to its stale snapshot',
  [(REV, '        with transaction(project_name) as pm:\n            current = next(',
    '        with pm.mutex:\n            current = next(')],
  ['tests/test_web.py::TestRetranslateTransaction', 'tests/test_web.py::TestRetranslateDetails'])
r('2 stopped task still ending does not own the state',
  [(T, "if task.state in ('running', 'paused') or task.threads_alive():\n            return task.pm",
    "if task.state in ('running', 'paused'):\n            return task.pm")],
  ['tests/test_web.py::TestRetranslateTransaction::test_edit_while_stopped_task_is_ending_is_kept'])
r('3 apply_outcome check and write not atomic',
  [(P, 'with self.mutex:  # the check and the write are one step', 'if True:')],
  ['tests/test_project.py::TestApplyOutcomeAtomic'])
r('3 mark_reviewed outside the mutex',
  [(P, '        with self.mutex:\n            seg = self._changeable(segment_id)\n            if text:',
    '        if True:\n            seg = self._changeable(segment_id)\n            if text:')],
  ['tests/test_project.py::TestApplyOutcomeAtomic::test_mark_reviewed_waits_for_the_mutex'])
r('4 timed-out join treated as an end',
  [(T, '            if existing.threads_alive():\n                raise', '            if False:\n                raise')],
  ['tests/test_web.py::TestOverlappingTasks'])
r('4 starts not serialized',
  [(T, '        with self._start_lock:\n            return self._start_locked',
    '        if True:\n            return self._start_locked')],
  ['tests/test_web.py::TestOverlappingTasks::test_simultaneous_starts'])
r('5 logging set up outside the try',
  [(T, '        handler = None\n        log_dir', '        handler = setup_project_logging(project_dir)\n        log_dir'),
   (T, '        try:\n            handler = setup_project_logging(project_dir)\n', '        try:\n')],
  ['tests/test_web.py::TestStartFailures::test_logging_setup_failure'])
r('5 coordinator start failure keeps the lock',
  [(T, '                if task.project_lock is None or not task.threads_alive():\n                    # Nothing started that would release it\n                    task.state = \'failed\'\n                    lock.release()',
    '                if task.project_lock is None:\n                    lock.release()')],
  ['tests/test_web.py::TestStartFailures::test_coordinator_start_failure'])
r('5 lock descriptor leaked after flock',
  [(P, '                except BaseException:\n                    os.close(fd)  # drops the flock taken above\n                    raise',
    '                except BaseException:\n                    raise')],
  ['tests/test_project.py::TestLockAcquireFailure'])
r('6 web coordinator failure does not join workers',
  [(T, '        for t in list(task._threads):\n            t.join()\n', '')],
  ['tests/test_web.py::TestCoordinatorFailure'])
r('6 web coordinator failure does not wake paused workers',
  [(T, "            task.cancel_event.set()\n            task.pause_event.set()\n        for t in",
    "            task.cancel_event.set()\n        for t in")],
  ['tests/test_web.py::TestCoordinatorFailure::test_save_failure_wakes_paused_worker'])
r('6 CLI supervisor failure does not join workers',
  [(CLI, '        try:\n            for t in threads:\n                t.join()\n        finally:',
    '        try:\n            pass\n        finally:')],
  ['tests/test_cli.py::TestCLISupervisorFailure'])
r('7 late run error after stop pauses the task',
  [(T, '                        if task.stop_event.is_set():\n                            break\n                        if task.cancel_event is generation:',
    '                        if task.cancel_event is generation:')],
  ['tests/test_web.py::TestStopThenLateRunError'])
r('8 KeyboardInterrupt leaves the child running',
  [(LLM, '        # Also when the program itself has ended: what it started may still run\n        stdout, stderr = _kill_group(proc)\n',
    '        stdout = stderr = None\n')],
  ['tests/test_llm.py::TestRunProcessExceptionalExit::test_keyboard_interrupt_kills_child'])
r('8 unbounded wait after the kill',
  [(LLM, 'proc.communicate(timeout=KILL_WAIT_SECONDS)', 'proc.communicate()')],
  ['tests/test_llm.py::TestRunProcessExceptionalExit::test_kill_wait_is_bounded_when_a_descendant_keeps_the_pipes'])
r('8 no exit hook',
  [(LLM, 'atexit.register(_kill_live_groups)', '')],
  ['tests/test_llm.py::TestRunProcessExceptionalExit::test_children_killed_when_interpreter_exits'])
r('9 failure log write error propagates',
  [(TR, '        try:\n            write_failure_log(log_dir, segment_id, outcome.model, outcome.failure)\n        except OSError as e:\n            logger.error("無法寫入失敗紀錄 %s: %s", segment_id, e)',
    '        write_failure_log(log_dir, segment_id, outcome.model, outcome.failure)')],
  ['tests/test_translator.py::TestDiagnosticWriteFailure', 'tests/test_web.py::TestWorkerUnexpectedErrors',
   'tests/test_cli.py::TestCLIUnexpectedErrors'])
r('9 web worker dies on an unexpected exception',
  [(TR, '    except Exception as e:  # noqa: BLE001 - the segment fails, the run goes on\n        logger.exception("未預期的錯誤: %s", seg.segment_id)\n        prompt = _request_text(engine, seg, glossary_text, context, brief) if context else \'\'\n        return unexpected_error_outcome(model, e, prompt)\n', '')],
  ['tests/test_web.py::TestWorkerUnexpectedErrors::test_unexpected_exception'])
r('9 CLI worker dies on an unexpected exception',
  [(TR, '    except Exception as e:  # noqa: BLE001 - the segment fails, the run goes on\n        logger.exception("未預期的錯誤: %s", seg.segment_id)\n        prompt = _request_text(engine, seg, glossary_text, context, brief) if context else \'\'\n        return unexpected_error_outcome(model, e, prompt)\n', '')],
  ['tests/test_cli.py::TestCLIUnexpectedErrors::test_unexpected_worker_exception',
   'tests/test_cli.py::TestCLILock::test_lock_released_on_error'])
r('10 no cancel check before the start',
  [(LLM, "    if cancel is not None and cancel.is_set():\n        raise CancelledError('cancelled')\n    deadline", '    deadline')],
  ['tests/test_llm.py::TestRunProcessCancelAndDeadline::test_cancelled_before_start_starts_nothing'])
r('10 wait not bounded by the deadline',
  [(LLM, 'wait = min(POLL_SECONDS, max(0.0, deadline - time.monotonic()))', 'wait = POLL_SECONDS'),
   (LLM, '            if time.monotonic() > deadline:\n                raise subprocess.TimeoutExpired(cmd, timeout,\n                                                output=stdout[:KEPT_OUTPUT_CHARS],\n                                                stderr=stderr[:KEPT_OUTPUT_CHARS])\n', '')],
  ['tests/test_llm.py::TestRunProcessCancelAndDeadline::test_deadline_not_stretched_by_poll_interval'])
r('10 output of a cancelled call accepted',
  [(LLM, "            # Finished: still refuse the output of a cancelled or late call\n            if cancel is not None and cancel.is_set():\n                raise CancelledError('cancelled')\n",
    '')],
  ['tests/test_llm.py::TestRunProcessCancelAndDeadline::test_output_of_call_cancelled_meanwhile_is_refused'])
r('11 review save ignores a refused save',
  [('src/web/templates/project/review.html',
    "        } else {\n            showSaveError(segId, r, data);\n        }\n    }))\n    .catch(err => showSaveError(segId, null, {error: err.message}));\n}\n\nfunction approveSegment",
    "        }\n    }));\n}\n\nfunction approveSegment")],
  ['tests/test_web.py::TestTemplatesHandleErrors'])
r('11 model select keeps a refused model',
  [('src/web/templates/project/translation.html', "                document.getElementById('model-select').value = previous;\n", '')],
  ['tests/test_web.py::TestTemplatesHandleErrors'])
r('22 retry button rendered by the server only',
  [('src/web/templates/project/translation.html', "                this.refreshFailedCount();\n                this.eventSource.close();\n                this.eventSource = null;\n            });\n\n            this.eventSource.addEventListener('error'",
    "                this.eventSource.close();\n                this.eventSource = null;\n            });\n\n            this.eventSource.addEventListener('error'")],
  ['tests/test_web.py::TestTemplatesHandleErrors'])
r('12 model list from the Claude module',
  [(TR, '    return [asdict(m) for m in llm.backend_for(backend).list_models()]',
    '    from src.llm.claude_cli import MODELS\n    return [asdict(m) for m in MODELS]')],
  ['tests/test_web.py::TestBackendModels', 'tests/test_cli.py::TestCLIBackendModels'])
r('12 engine default model hard-coded',
  [(TR, 'self.model = model if model is not None else self.backend.default_model',
    "self.model = model if model is not None else 'sonnet'")],
  ['tests/test_translator.py::TestEngineDefaultModel'])
r('12 auth hint hard-coded in the page',
  [('src/web/templates/project/translation.html', '{{ auth_hint|tojson }}',
    "'Claude CLI 認證已過期，請在終端執行 claude /login 重新登入'")],
  ['tests/test_web.py::TestBackendModels::test_pages_have_no_claude_specifics'])
r('13 issue detail unbounded',
  [(TR, 'detail=message[:ERROR_MESSAGE_CHARS], blocking=True)]', 'detail=message, blocking=True)]')],
  ['tests/test_translator.py::TestBoundedErrorText'])
r('13 messages in the failure log unbounded',
  [(TR, "f'=== attempt {i}: {message[:ERROR_MESSAGE_CHARS]} ==='", "f'=== attempt {i}: {message} ==='"),
   (TR, "f'error: {str(error)[:ERROR_MESSAGE_CHARS]}'", "f'error: {error}'")],
  ['tests/test_translator.py::TestBoundedErrorText'])
r('13 stderr quoted whole in the Claude error',
  [('src/llm/claude_cli.py', "f'{stderr.strip()[:ERROR_CHARS]}')", "f'{stderr.strip()}')")],
  ['tests/test_llm.py::TestClassificationOfReplies::test_error_messages_bounded'])
r('14 timeout drops the output',
  [(LLM, "                                 raw=self.redact(_text(e.output) + _text(e.stderr))[:KEPT_OUTPUT_CHARS])", "                                 )")],
  ['tests/test_llm.py::TestTimeoutKeepsOutput'])
r('15 missing program is a per-segment error',
  [(LLM, '            raise BackendUnavailableError(', '            raise LLMError(')],
  ['tests/test_llm.py::TestBackendUnavailable', 'tests/test_llm.py::TestCLIBackendComplete::test_missing_program_is_not_transient'])
r('16 dropped result sends a success event',
  [(T, '            logger.info("段落已在翻譯期間被編輯，捨棄結果: %s", res.segment_id)\n            outcome = None\n',
    '            logger.info("段落已在翻譯期間被編輯，捨棄結果: %s", res.segment_id)\n')],
  ['tests/test_web.py::TestQueuedEdits::test_dropped_result_sends_no_success_event'])
r('17 revision taken when the worker picks the segment',
  [(T, '                revision, queued_status = task.queued.get(\n                    seg.segment_id, (seg.revision, seg.status))',
    '                revision, queued_status = seg.revision, seg.status')],
  ['tests/test_web.py::TestQueuedEdits::test_queued_segment_edited_before_pickup'])
r('17 edit keeps FAILED and issues',
  [(P, '            if seg.status == SegmentStatus.FAILED:\n                seg.status = SegmentStatus.TRANSLATED\n            seg.issues = list(issues or [])\n',
    '')],
  ['tests/test_project.py::TestManualEditClearsFailure', 'tests/test_web.py::TestEditRemovesFailureLog'])
r('18 delete ignores a running task',
  [('src/web/blueprints/projects.py', "        if TaskManager().task_active(project_name):\n            return jsonify({'error': '專案正在翻譯中，請先停止翻譯再刪除'}", "        if False:\n            return jsonify({'error': '專案正在翻譯中，請先停止翻譯再刪除'}")],
  ['tests/test_web.py::TestDeleteGuard::test_delete_refused_while_task_runs'])
r('18 delete ignores the lock of another process',
  [('src/web/blueprints/projects.py', '            lock.acquire()\n        except ProjectLockedError as e:',
    '            pass\n        except ProjectLockedError as e:'),
   ('src/web/blueprints/projects.py', '        finally:\n            lock.release()', '        finally:\n            pass')],
  ['tests/test_web.py::TestDeleteGuard::test_delete_refused_while_other_process_holds_lock'])
r('19 extraction stop drops earlier terms',
  [('src/glossary/term_extractor.py', 'raise ExtractionError(str(e), terms=_final(groups, {})) from e', 'raise')],
  ['tests/test_glossary.py::TestExtractionStopKeepsTerms', 'tests/test_cli.py::TestCLIGlossaryExtractStop',
   'tests/test_web.py::TestTermExtractionStop'])
r('19 CLI extraction stop not caught',
  [(CLI, '    except ExtractionError as e:\n        # Keep what the finished batches found', '    except ZeroDivisionError as e:\n        # Keep what the finished batches found')],
  ['tests/test_cli.py::TestCLIGlossaryExtractStop'])
r('19 long successful replies searched for error phrases',
  [('src/llm/claude_cli.py', 'if result.returncode != 0 or len(stdout) <= SHORT_REPLY_CHARS:', 'if True:')],
  ['tests/test_llm.py::TestClassificationOfReplies'])
r('19 short successful replies not searched',
  [('src/llm/claude_cli.py', 'if result.returncode != 0 or len(stdout) <= SHORT_REPLY_CHARS:', 'if result.returncode != 0:')],
  ['tests/test_llm.py::TestClassificationOfReplies', 'tests/test_llm.py::TestClaudeCLIClassification'])
r('20 no round trip in the check',
  [('scripts/verify/phase1_load_projects.py', "    if result == 'differs':\n        problems.append", "    if False:\n        problems.append")],
  ['tests/test_verify_scripts.py::TestLoadCheckRoundTrip'])
r('21 file name without digest',
  [(TR, "f'{safe_id}-{digest}.txt'", "f'{safe_id}.txt'")],
  ['tests/test_translator.py::TestFailureLogNames'])
r('24 edit leaves the failure log',
  [(REV, "            # A person's version replaces the failed one: its diagnostics go too\n            remove_failure_log(_log_dir(pm), segment_id)\n", '')],
  ['tests/test_web.py::TestEditRemovesFailureLog'])
r('24 approve leaves the failure log',
  [(REV, "            pm.save()\n            remove_failure_log(_log_dir(pm), segment_id)\n            # the warnings",
    "            pm.save()\n            # the warnings")],
  ['tests/test_web.py::TestEditRemovesFailureLog'])
r('25 call spent on a segment edited after queueing',
  [(T, '                if skip:\n', '                if False:\n')],
  ['tests/test_web.py::TestQueuedEdits::test_queued_segment_edited_before_pickup'])
r('25 explicitly queued reviewed segment skipped',
  [(T, "                        seg.status == SegmentStatus.REVIEWED\n                        and queued_status != SegmentStatus.REVIEWED)",
    "                        seg.status == SegmentStatus.REVIEWED)")],
  ['tests/test_web.py::TestQueuedEdits::test_queued_reviewed_segment_is_translated_when_asked'])


# --- Review round 2 ---------------------------------------------------------------

CL, PR, TRL, TPL = ('src/llm/claude_cli.py', 'src/web/blueprints/projects.py',
                    'src/web/blueprints/translation.py', 'src/web/templates/project/translation.html')

r('R2-1 transaction without the file lock',
  [(T, '            with ProjectLock(project_dir):\n                yield', '            if True:\n                yield')],
  ['tests/test_web.py::TestRound2TransactionAcrossProcesses'])
r('R2-2 delete checks the task outside the mutex',
  [(PR, "    with project_mutex(project_dir):\n        if TaskManager().task_active(project_name):\n            return jsonify({'error': '專案正在翻譯中，請先停止翻譯再刪除'}), 409\n",
    "    if TaskManager().task_active(project_name):\n        return jsonify({'error': '專案正在翻譯中，請先停止翻譯再刪除'}), 409\n    with project_mutex(project_dir):\n")],
  ['tests/test_web.py::TestRound2DeleteGuardInsideMutex'])
r('R2-3 CLI workers started outside the protected block',
  [(CLI, '    try:\n        # Started inside the try: if a later start fails or is interrupted,\n        # the workers already running are stopped and joined below\n        for i in range(actual_workers):\n            # Daemon: a second Ctrl-C ends the process without waiting for them\n            t = threading.Thread(target=worker, args=(i,), daemon=True)\n            t.start()\n            threads.append(t)\n\n        while',
    '    for i in range(actual_workers):\n        t = threading.Thread(target=worker, args=(i,), daemon=True)\n        t.start()\n        threads.append(t)\n    try:\n        while')],
  ['tests/test_cli.py::TestRound2WorkerStartup'])
r('R2-4 every start error stops the run',
  [(LLM, "            if isinstance(e, (FileNotFoundError, PermissionError)) or e.errno == errno.ENOEXEC:",
    '            if True:')],
  ['tests/test_llm.py::TestRound2StartErrors', 'tests/test_translator.py::TestRound2StartErrorFailsSegment'])
r('R2-5 unexpected exception escapes the entry point',
  [(TR, '    except Exception as e:  # noqa: BLE001 - the segment fails, the run goes on\n        logger.exception("未預期的錯誤: %s", seg.segment_id)\n        prompt = _request_text(engine, seg, glossary_text, context, brief) if context else \'\'\n        return unexpected_error_outcome(model, e, prompt)\n', '')],
  ['tests/test_translator.py::TestRound2UnexpectedErrorInEntryPoint',
   'tests/test_cli.py::TestCLIUnexpectedErrors::test_unexpected_worker_exception'])
r('R2-6 group left alone when the parent has exited',
  [(LLM, '        stdout, stderr = _kill_group(proc)\n        if isinstance(e',
    "        stdout = stderr = ''\n        if proc.returncode is None:\n            stdout, stderr = _kill_group(proc)\n        if isinstance(e")],
  ['tests/test_llm.py::TestRound2GroupKilledAfterParentExit'])
r('R2-7 start not refused after the exit hook',
  [(LLM, "        if _shutting_down:\n            raise CancelledError('shutting down')\n", '')],
  ['tests/test_llm.py::TestRound2ExitHook'])
r('R2-7 exit hook sets no flag',
  [(LLM, '        _shutting_down = True\n        groups', '        groups')],
  ['tests/test_llm.py::TestRound2ExitHook'])
r('R2-9 output dropped when the pipes are given up',
  [(LLM, '        stdout, stderr = _decoded(e.output), _decoded(e.stderr)', "        stdout = stderr = ''")],
  ['tests/test_llm.py::TestRound2PartialOutputAfterKill',
   'tests/test_llm.py::TestRunProcessExceptionalExit::test_kill_wait_is_bounded_when_a_descendant_keeps_the_pipes'])
r('R2-10 web success removes the log before acceptance',
  [(T, '        outcome = res.outcome\n        if outcome is None:',
    "        outcome = res.outcome\n        if outcome is not None and outcome.status == SegmentStatus.TRANSLATED:\n            record_failure_log(os.path.join(pm.project_dir, 'log'), res.segment_id, outcome)\n        if outcome is None:")],
  ['tests/test_web.py::TestRound2FailureLogAtAcceptance::test_stale_success_keeps_newer_failure_log'])
r('R2-10 re-translate records the log before acceptance and save',
  [(REV, '                                    brief=brief)\n',
    "                                    brief=brief)\n        record_failure_log(os.path.join(project_dir, 'log'), segment_id, outcome)\n")],
  ['tests/test_web.py::TestRound2FailureLogAtAcceptance::test_success_whose_save_is_refused_keeps_the_log',
   'tests/test_web.py::TestRound2FailureLogAtAcceptance::test_stale_retranslation_writes_no_log'])
r('R2-17 web failure log written before acceptance',
  [(T, '        outcome = res.outcome\n        if outcome is None:',
    "        outcome = res.outcome\n        if outcome is not None:\n            record_failure_log(os.path.join(pm.project_dir, 'log'), res.segment_id, outcome)\n        if outcome is None:")],
  ['tests/test_web.py::TestRound2FailureLogAtAcceptance::test_stale_failure_writes_no_log'])
r('R2-17 edit removes the log outside the transaction',
  [(REV, "            pm.save()\n            # A person's version replaces the failed one: its diagnostics go too\n            remove_failure_log(_log_dir(pm), segment_id)\n            answer = _answer(pm, gm, issues, status='ok', segment_id=segment_id)\n    except ValueError as e:\n        return jsonify({'error': str(e)}), 404\n",
    "            pm.save()\n            answer = _answer(pm, gm, issues, status='ok', segment_id=segment_id)\n    except ValueError as e:\n        return jsonify({'error': str(e)}), 404\n    remove_failure_log(_log_dir(pm), segment_id)\n")],
  ['tests/test_web.py::TestRound2FailureLogAtAcceptance::test_edit_removes_the_log_inside_the_transaction'])
r('R2-11 run-level error not kept on the task',
  [(T, '        task.error = str(error)  # for a page opened later\n', '')],
  ['tests/test_web.py::TestRound2RunErrorVisible'])
r('R2-11 resume keeps the old error',
  [(T, '            task.error = None  # the run-level error that paused it\n', '')],
  ['tests/test_web.py::TestRound2RunErrorVisible::test_resume_clears_it'])
r('R2-12 stored model not shown',
  [(TPL, "                if (!this.availableModels.some(m => m.key === this.modelKey())) {", '                if (false) {')],
  ['tests/test_web.py::TestRound2StoredModelOption'])
r('R2-13 Unicode in the file name',
  [(TR, "re.sub(r'[^A-Za-z0-9_.-]', '_', segment_id)", "re.sub(r'[^\\w.-]', '_', segment_id)")],
  ['tests/test_translator.py::TestRound2FailureLogNameBytes'])
r('R2-15 echoed phrase taken for an error',
  [(CL, "        echoed = prompt.lower() if result.returncode == 0 else ''", "        echoed = ''")],
  ['tests/test_llm.py::TestRound2RequestEcho', 'tests/test_translator.py::TestRound2CodeEchoingAnErrorPhrase'])
r('R2-16 no project check before the lock',
  [(T, "            # No lock file in a directory that is not (or no longer) a project\n            if not os.path.isfile(os.path.join(project_dir, ProjectManager.PROJECT_FILE)):\n                raise FileNotFoundError('專案不存在')\n", '')],
  ['tests/test_web.py::TestRound2StartChecks'])
r('R2-16 file lock before the mutex',
  [(T, '        with project_mutex(project_dir):\n            # No lock file', '        lock.acquire()\n        with project_mutex(project_dir):\n            # No lock file'),
   (T, '            lock.acquire()  # raises ProjectLockedError\n', '')],
  ['tests/test_web.py::TestRound2StartChecks::test_lock_taken_under_the_mutex'])
r('R2-19 temporary descriptor leaked by save',
  [(P, "                with open(fd, 'w', encoding='utf-8') as f:  # closes fd on any error\n                    # mkstemp creates the file 0600: keep the mode project.json had\n                    try:\n                        mode = os.stat(self.project_file).st_mode & 0o777\n                    except FileNotFoundError:\n                        mode = 0o644\n                    os.fchmod(fd, mode)\n                    f.write(text)",
    "                try:\n                    mode = os.stat(self.project_file).st_mode & 0o777\n                except FileNotFoundError:\n                    mode = 0o644\n                os.fchmod(fd, mode)\n                with open(fd, 'w', encoding='utf-8') as f:\n                    f.write(text)")],
  ['tests/test_project.py::TestRound2DescriptorLeaks'])
r('R2-19 lock descriptor leaked when the holder cannot be read',
  [(P, "                    try:\n                        holder = os.read(fd, 32).decode(errors='replace').strip()\n                    finally:\n                        os.close(fd)\n",
    '                    holder = os.read(fd, 32).decode().strip()\n                    os.close(fd)\n')],
  ['tests/test_project.py::TestRound2DescriptorLeaks'])
# Since Phase 7 the saved part of the report is DebouncedSaver.pending, which
# only accepted results enter; the queue drain is where the old report survives
r('R2-20 stale and skipped results reported as unsaved',
  [(T, '                if (res.outcome is not None\n                        and task.pm._find(res.segment_id).revision == res.revision):\n', '                if True:\n')],
  ['tests/test_web.py::TestRound2UnsavedReport::test_skipped_result_in_queue_not_reported'])
r('R2-21 rate-limit message cut from long stdout',
  [(CL, '            source = stderr if _RATE_LIMIT_PATTERN.search(stderr) else stdout\n            return RateLimitError(source.strip()[:ERROR_CHARS])',
    '            return RateLimitError(searched.strip()[:ERROR_CHARS])')],
  ['tests/test_llm.py::TestRound2RateLimitMessage'])

FAKE = 'src/llm/fake.py'
r('R3-1 project replaced before the lock is taken',
  [(PR, "        with project_mutex(project_dir):\n            if TaskManager().task_active(project_name):\n                return jsonify({'error': '專案正在翻譯中，請先停止翻譯再重新建立'}), 409\n            # Under the mutex: a delete cannot remove it before the lock\n            os.makedirs(project_dir, exist_ok=True)\n            try:\n                lock = ProjectLock(project_dir)\n                lock.acquire()\n            except ProjectLockedError as e:\n                return jsonify({'error': str(e)}), 409\n            try:",
    "        os.makedirs(project_dir, exist_ok=True)\n        if True:\n            lock = ProjectLock(project_dir)\n            try:")],
  ['tests/test_web.py::TestRound3ReplaceProjectUnderLock'])
r('R3-2 no Start for a stopped task',
  [(TPL, " || state === 'failed' || state === 'stopped'\">", " || state === 'failed'\">")],
  ['tests/test_web.py::TestRound3StoppedTaskRestartable'])
r('R3-2 stopped event leaves state and stream',
  [(TPL, "                this.state = 'stopped';\n                this.autoResume = false;\n                this.currentSegment = null;\n                this.refreshFailedCount();\n                this.eventSource.close();\n                this.eventSource = null;\n",
    '                this.refreshFailedCount();\n')],
  ['tests/test_web.py::TestRound3StoppedTaskRestartable'])
r('R3-3 echo rule hides stderr',
  [(CL, '            return (pattern.search(stderr) is not None\n                    or any(m.group(0).lower() not in echoed for m in pattern.finditer(searched)))',
    '            return any(m.group(0).lower() not in echoed for m in pattern.finditer(searched + stderr))')],
  ['tests/test_llm.py::TestRound3StderrNotEchoSuppressed'])
r('R3-4 resource errors at start fail the segment',
  [(LLM, '            if e.errno in RESOURCE_ERRNOS:', '            if False:')],
  ['tests/test_llm.py::TestRound3ResourceErrors'])
r('R3-5 unstarted workers joined',
  [(T, '                try:\n                    t.start()\n                except BaseException:\n                    with task._lock:\n                        task.active_workers -= num_workers - i  # never started\n                    raise\n                task._threads.append(t)\n',
    '                task._threads.append(t)\n                t.start()\n')],
  ['tests/test_web.py::TestRound3WorkerStartFailure'])
r('R3-6 initial status without the error',
  [(TRL, "'total': task.total, 'error': task.error,", "'total': task.total,")],
  ['tests/test_web.py::TestRound3InitialStatusError'])
r('R3-6 cursor taken after the snapshot',
  [(TRL, '        cursor = task.progress.cursor_end\n        # Initial status', '        # Initial status'),
   (TRL, '        yield f"event: status\\ndata: {json.dumps(status, ensure_ascii=False)}\\n\\n"\n',
    '        yield f"event: status\\ndata: {json.dumps(status, ensure_ascii=False)}\\n\\n"\n        cursor = task.progress.cursor_end\n')],
  ['tests/test_web.py::TestRound3InitialStatusError::test_event_after_snapshot_delivered'])
r('R3-6 page ignores the initial error',
  [(TPL, '                    if (data.error) this.error = data.error;\n', '')],
  ['tests/test_web.py::TestRound3InitialStatusError'])
r('R3-7 strict decoding of the output',
  [(LLM, "encoding='utf-8', errors='replace',", 'text=True,')],
  ['tests/test_llm.py::TestRound3UndecodableOutput', 'tests/test_translator.py::TestRound3UnexpectedFailureLog'])
r('R3-7 unexpected failure without diagnostics',
  [(TR, "blocking=True)],\n        failure=TranslationError(detail, code='unexpected', prompt=prompt,\n                                 attempts=[(f'{type(error).__name__}: {error}', trace)]))",
    'blocking=True)])')],
  ['tests/test_translator.py::TestRound3UnexpectedFailureLog',
   'tests/test_translator.py::TestRound2EntryPointWritesNoFile'])
r('R3-7 request not in the unexpected-failure log',
  [(TR, "        prompt = _request_text(engine, seg, glossary_text, context, brief) if context else ''",
    "        prompt = ''")],
  ['tests/test_translator.py::TestRound3UnexpectedFailureLog'])
r('R3-8 stale result in the failure drain reported as unsaved',
  [(T, '                if (res.outcome is not None\n                        and task.pm._find(res.segment_id).revision == res.revision):\n',
    '                if res.outcome is not None:\n')],
  ['tests/test_web.py::TestRound3StaleResultInFailureDrain'])
r('R3-9 web run goes on after five unexpected failures',
  [(T, '                if stop and streak_stop is None:\n', '                if False:\n')],
  ['tests/test_web.py::TestRound3UnexpectedStreakWeb'])
r('R3-9 sequential CLI goes on after five unexpected failures',
  [(CLI, '            stop = streak.add(outcome)\n            if stop:\n                raise _RunStopped(stop)\n', '')],
  ['tests/test_cli.py::TestCLIRound3UnexpectedStreak'])
r('R3-9 parallel CLI goes on after five unexpected failures',
  [(CLI, '            stop_errors.append(stop)\n            error_event.set()\n', '            pass\n')],
  ['tests/test_cli.py::TestCLIRound3UnexpectedStreak::test_parallel'])
r('R3-9 streak not reset by another result',
  [(TR, '        else:\n            self.count = 0\n        if self.count', '        if self.count')],
  ['tests/test_translator.py::TestRound3UnexpectedStreak', 'tests/test_web.py::TestRound3UnexpectedStreakWeb',
   'tests/test_cli.py::TestCLIRound3UnexpectedStreak'])
r('R3-10 fake over-call is an ordinary exception',
  [(FAKE, "raise FakeBackendExhausted('FakeBackend: no scripted reply left')",
    "raise AssertionError('FakeBackend: no scripted reply left')")],
  ['tests/test_llm.py::TestRound3FakeOverCall'])
r('R3-11 transaction takes the lock of a missing project',
  [(T, "            if not os.path.isfile(os.path.join(project_dir, ProjectManager.PROJECT_FILE)):\n                raise FileNotFoundError('專案不存在')\n            with ProjectLock",
    '            with ProjectLock')],
  ['tests/test_web.py::TestRound3MissingProject'])
r('R3-11 delete takes the lock of a directory without project.json',
  [(PR, "        if not os.path.isfile(os.path.join(project_dir, ProjectManager.PROJECT_FILE)):\n            return jsonify({'error': '專案不存在'}), 404\n", '')],
  ['tests/test_web.py::TestRound3MissingProject'])
r('R3-11 review endpoints answer 500 for a missing project',
  [(REV, '@review_bp.errorhandler(FileNotFoundError)\n', '')],
  ['tests/test_web.py::TestRound3MissingProject'])
r('R3-12 late finish keeps unbounded output',
  [(LLM, 'output=stdout[:KEPT_OUTPUT_CHARS],\n                                                stderr=stderr[:KEPT_OUTPUT_CHARS])',
    'output=stdout, stderr=stderr)')],
  ['tests/test_llm.py::TestRound3TimeoutOutputBounded'])
r('R3-12 timeout raw not bounded',
  [(LLM, 'raw=self.redact(_text(e.output) + _text(e.stderr))[:KEPT_OUTPUT_CHARS])', 'raw=self.redact(_text(e.output) + _text(e.stderr)))')],
  ['tests/test_llm.py::TestRound3TimeoutOutputBounded'])

r('R4-1 re-translation applied to a replaced project',
  [(REV, '            if current is None or (current.html, current.text) != source:\n',
    '            if current is None:\n')],
  ['tests/test_web.py::TestRound4RetranslateAcrossReplacement'])
r('R4-2 worker added on resume listed and counted before its start',
  [(T, "            task.active_workers += 1\n            try:\n                t.start()\n            except Exception as e:\n                # Not started: neither listed nor counted\n                task.active_workers -= 1\n                task.error = f'無法增加 worker：{e}'\n                return False\n            task._threads.append(t)\n",
    '            task._threads.append(t)\n            task.active_workers += 1\n            t.start()\n')],
  ['tests/test_web.py::TestRound4ResumeWorkerStartFailure'])
r('R4-3 streak stop discards in-flight results',
  [(T, '                    streak_stop = stop\n                    with task._lock:\n                        task.stop_event.set()  # resume_translation refuses it now\n                        task.pause_event.set()\n',
    '                    raise RuntimeError(stop)\n')],
  ['tests/test_web.py::TestRound4StreakKeepsInFlightResults'])
r('R4-4 source.epub written before the project is saved',
  [(PR, '                    shutil.copy2(epub_path, tmp_epub_path)\n',
    '                    shutil.copy2(epub_path, final_epub_path)\n                    shutil.copy2(epub_path, tmp_epub_path)\n')],
  ['tests/test_web.py::TestRound4ReplacementFiles'])
r('R4-5 stream of an ended task waits for events',
  [(TRL, "        if status['state'] in ('completed', 'failed', 'stopped'):\n            return  # ended before this stream opened: its last event is gone\n", '')],
  ['tests/test_web.py::TestRound4StreamAfterEnd'])
r('R4-5 page keeps the stream of an ended task',
  [(TPL, '                if (ended) {\n                    this.currentSegment = null;\n                    this.refreshFailedCount();\n                    this.eventSource.close();\n                    this.eventSource = null;\n                }\n', '')],
  ['tests/test_web.py::TestRound4StreamAfterEnd'])
r('R4-6 project directory created before the mutex',
  [(PR, '        # A project of the same name is replaced', '        os.makedirs(project_dir, exist_ok=True)\n        # A project of the same name is replaced'),
   (PR, '            # Under the mutex: a delete cannot remove it before the lock\n            os.makedirs(project_dir, exist_ok=True)\n', '')],
  ['tests/test_web.py::TestRound4ReplacementFiles'])
r('R4-6 old failure logs kept after a replacement',
  [(PR, "                shutil.rmtree(os.path.join(project_dir, 'log', 'failed'), ignore_errors=True)\n", '')],
  ['tests/test_web.py::TestRound4ReplacementFiles'])

undetected = 0
for name, edits, tests in R:
    if only and not any(name.startswith(o) for o in only):
        continue
    d = tempfile.mkdtemp()
    try:
        archive = subprocess.run(['git', '-C', REPO, 'archive', 'HEAD'], capture_output=True,
                                 check=True).stdout
        subprocess.run(['tar', '-x', '-C', d], input=archive, check=True)
        for f, old, new in edits:
            path = os.path.join(d, f)
            text = open(path, encoding='utf-8').read()
            n = text.count(old)
            if n != 1:
                print(f'BAD  {name}: {f} has the old text {n} times')
                undetected += 1
                break
            open(path, 'w', encoding='utf-8').write(text.replace(old, new))
        else:
            p = subprocess.run([PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *tests],
                               cwd=d, capture_output=True, text=True, timeout=600,
                               env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
            last = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-200:]
            ok = p.returncode == 1 and ' failed' in last
            undetected += not ok
            print(f'{"OK  " if ok else "MISS"} {name}: {last}', flush=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)
print(f'reverts not detected: {undetected}')
sys.exit(1 if undetected else 0)
