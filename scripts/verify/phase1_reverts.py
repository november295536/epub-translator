#!/usr/bin/env python3
"""Phase 1, review round 1: each fixed behaviour is guarded by a test.

Puts each old behaviour back, one at a time, into a copy of the committed
code (git archive HEAD, in a temporary directory removed afterwards) and
runs the tests that guard it. Every revert must make at least one of them
fail. Prints OK/MISS per revert and the number not detected.

Usage:
    python3 scripts/verify/phase1_reverts.py [name-prefix ...]

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
  [(REV, '        with transaction(project_name) as pm:\n            if not pm.apply_outcome',
    '        with pm.mutex:\n            if not pm.apply_outcome')],
  ['tests/test_web.py::TestRetranslateTransaction', 'tests/test_web.py::TestRetranslateDetails'])
r('2 stopped task still ending does not own the state',
  [(T, "if task.state in ('running', 'paused') or task.threads_alive():\n            return task.pm",
    "if task.state in ('running', 'paused'):\n            return task.pm")],
  ['tests/test_web.py::TestRetranslateTransaction::test_edit_while_stopped_task_is_ending_is_kept'])
r('3 apply_outcome check and write not atomic',
  [(P, 'with self.mutex:  # the check and the write are one step', 'if True:')],
  ['tests/test_project.py::TestApplyOutcomeAtomic'])
r('3 mark_reviewed outside the mutex',
  [(P, '        with self.mutex:\n            seg = self._find(segment_id)\n            if text:',
    '        if True:\n            seg = self._find(segment_id)\n            if text:')],
  ['tests/test_project.py::TestApplyOutcomeAtomic::test_mark_reviewed_waits_for_the_mutex'])
r('4 timed-out join treated as an end',
  [(T, '            if existing.threads_alive():\n                raise', '            if False:\n                raise')],
  ['tests/test_web.py::TestOverlappingTasks'])
r('4 starts not serialized',
  [(T, '        with self._start_lock:\n            return self._start_locked',
    '        if True:\n            return self._start_locked')],
  ['tests/test_web.py::TestOverlappingTasks::test_simultaneous_starts'])
r('5 logging set up outside the try',
  [(T, '        handler = None\n        unsaved = []', '        handler = setup_project_logging(project_dir)\n        unsaved = []'),
   (T, '            handler = setup_project_logging(project_dir)\n            pm = task.pm', '            pm = task.pm')],
  ['tests/test_web.py::TestStartFailures::test_logging_setup_failure'])
r('5 coordinator start failure keeps the lock',
  [(T, '            if task.project_lock is None or not task.threads_alive():\n                # Nothing started that would release it\n                task.state = \'failed\'\n                lock.release()',
    '            if task.project_lock is None:\n                lock.release()')],
  ['tests/test_web.py::TestStartFailures::test_coordinator_start_failure'])
r('5 lock descriptor leaked after flock',
  [(P, '                except BaseException:\n                    os.close(fd)  # drops the flock taken above\n                    raise',
    '                except BaseException:\n                    raise')],
  ['tests/test_project.py::TestLockAcquireFailure'])
r('6 web coordinator failure does not join workers',
  [(T, '        for t in list(task._threads):\n            t.join()\n', '')],
  ['tests/test_web.py::TestCoordinatorFailure'])
r('6 web coordinator failure does not wake paused workers',
  [(T, "            task.stop_event.set()\n            task.pause_event.set()\n        for t in",
    "            task.stop_event.set()\n        for t in")],
  ['tests/test_web.py::TestCoordinatorFailure::test_save_failure_wakes_paused_worker'])
r('6 CLI supervisor failure does not join workers',
  [(CLI, '        click.echo("翻譯中斷，等待進行中的段落結束……")\n        for t in threads:\n            t.join()\n',
    '        click.echo("翻譯中斷，等待進行中的段落結束……")\n')],
  ['tests/test_cli.py::TestCLISupervisorFailure'])
r('7 late run error after stop pauses the task',
  [(T, '                        if task.stop_event.is_set():\n                            break\n                        # Only first',
    '                        # Only first')],
  ['tests/test_web.py::TestStopThenLateRunError'])
r('8 KeyboardInterrupt leaves the child running',
  [(LLM, '    except BaseException:\n        if proc.returncode is None:\n            _kill_group(proc)\n        raise\n',
    '    except BaseException:\n        raise\n')],
  ['tests/test_llm.py::TestRunProcessExceptionalExit::test_keyboard_interrupt_kills_child'])
r('8 unbounded wait after the kill',
  [(LLM, 'proc.communicate(timeout=KILL_WAIT_SECONDS)', 'proc.communicate()')],
  ['tests/test_llm.py::TestRunProcessExceptionalExit::test_kill_wait_is_bounded_when_a_descendant_keeps_the_pipes'])
r('8 no exit hook',
  [(LLM, 'atexit.register(_kill_live_groups)', '')],
  ['tests/test_llm.py::TestRunProcessExceptionalExit::test_children_killed_when_interpreter_exits'])
r('9 failure log write error propagates',
  [(TR, '            try:\n                write_failure_log(log_dir, seg.segment_id, model, e)\n            except OSError as log_error:\n                logger.error("無法寫入失敗紀錄 %s: %s", seg.segment_id, log_error)',
    '            write_failure_log(log_dir, seg.segment_id, model, e)')],
  ['tests/test_translator.py::TestDiagnosticWriteFailure', 'tests/test_web.py::TestWorkerUnexpectedErrors',
   'tests/test_cli.py::TestCLIUnexpectedErrors'])
r('9 web worker dies on an unexpected exception',
  [(T, '                    except Exception as e:  # noqa: BLE001 - the segment fails, the run goes on\n                        logger.exception("[%s] 未預期的錯誤: %s", tag, seg.segment_id)\n                        outcome = unexpected_error_outcome(engine.model, e)\n',
    '')],
  ['tests/test_web.py::TestWorkerUnexpectedErrors::test_unexpected_exception'])
r('9 CLI worker dies on an unexpected exception',
  [(CLI, '            except Exception as e:  # noqa: BLE001 - the segment fails, the run goes on\n                outcome = unexpected_error_outcome(engine.model, e)\n', '')],
  ['tests/test_cli.py::TestCLIUnexpectedErrors::test_unexpected_worker_exception',
   'tests/test_cli.py::TestCLILock::test_lock_released_on_error'])
r('10 no cancel check before the start',
  [(LLM, "    if cancel is not None and cancel.is_set():\n        raise CancelledError('cancelled')\n    deadline", '    deadline')],
  ['tests/test_llm.py::TestRunProcessCancelAndDeadline::test_cancelled_before_start_starts_nothing'])
r('10 wait not bounded by the deadline',
  [(LLM, 'wait = min(POLL_SECONDS, max(0.0, deadline - time.monotonic()))', 'wait = POLL_SECONDS'),
   (LLM, '            if time.monotonic() > deadline:\n                raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)\n', '')],
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
  [('src/web/templates/project/translation.html', '                this.model = previous;\n', '')],
  ['tests/test_web.py::TestTemplatesHandleErrors'])
r('22 retry button rendered by the server only',
  [('src/web/templates/project/translation.html', "                this.refreshFailedCount();\n                this.eventSource.close();", "                this.eventSource.close();")],
  ['tests/test_web.py::TestTemplatesHandleErrors'])
r('12 model list from the Claude module',
  [(TR, '    return [asdict(m) for m in llm.default_backend().list_models()]',
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
  [(TR, 'detail=str(e)[:ERROR_MESSAGE_CHARS], blocking=True)', 'detail=str(e), blocking=True)')],
  ['tests/test_translator.py::TestBoundedErrorText'])
r('13 messages in the failure log unbounded',
  [(TR, "f'=== attempt {i}: {message[:ERROR_MESSAGE_CHARS]} ==='", "f'=== attempt {i}: {message} ==='"),
   (TR, "f'error: {str(error)[:ERROR_MESSAGE_CHARS]}'", "f'error: {error}'")],
  ['tests/test_translator.py::TestBoundedErrorText'])
r('13 stderr quoted whole in the Claude error',
  [('src/llm/claude_cli.py', "f'{stderr.strip()[:ERROR_CHARS]}')", "f'{stderr.strip()}')")],
  ['tests/test_llm.py::TestClassificationOfReplies::test_error_messages_bounded'])
r('14 timeout drops the output',
  [(LLM, "                                 raw=_text(e.output) + _text(e.stderr))", "                                 )")],
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
  [(P, '            if seg.status == SegmentStatus.FAILED:\n                seg.status = SegmentStatus.TRANSLATED\n            seg.issues = []\n',
    '')],
  ['tests/test_project.py::TestManualEditClearsFailure', 'tests/test_web.py::TestEditRemovesFailureLog'])
r('18 delete ignores a running task',
  [('src/web/blueprints/projects.py', '    if TaskManager().task_active(project_name):', '    if False:')],
  ['tests/test_web.py::TestDeleteGuard::test_delete_refused_while_task_runs'])
r('18 delete ignores the lock of another process',
  [('src/web/blueprints/projects.py', '            lock.acquire()\n        except ProjectLockedError as e:',
    '            pass\n        except ProjectLockedError as e:'),
   ('src/web/blueprints/projects.py', '        finally:\n            lock.release()', '        finally:\n            pass')],
  ['tests/test_web.py::TestDeleteGuard::test_delete_refused_while_other_process_holds_lock'])
r('19 extraction stop drops earlier terms',
  [('src/glossary/term_extractor.py', 'raise ExtractionError(str(e), terms=found()) from e', 'raise')],
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
  [(REV, '    # A person\'s version replaces the failed one: its diagnostics go too\n    remove_failure_log(_log_dir(pm), segment_id)\n', '')],
  ['tests/test_web.py::TestEditRemovesFailureLog'])
r('24 approve leaves the failure log',
  [(REV, "        return jsonify({'error': str(e)}), 404\n    remove_failure_log(_log_dir(pm), segment_id)\n    return jsonify({'status': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/retranslate'",
    "        return jsonify({'error': str(e)}), 404\n    return jsonify({'status': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/retranslate'")],
  ['tests/test_web.py::TestEditRemovesFailureLog'])
r('25 call spent on a segment edited after queueing',
  [(T, '                if skip:\n', '                if False:\n')],
  ['tests/test_web.py::TestQueuedEdits::test_queued_segment_edited_before_pickup'])
r('25 explicitly queued reviewed segment skipped',
  [(T, "                        seg.status == SegmentStatus.REVIEWED\n                        and queued_status != SegmentStatus.REVIEWED)",
    "                        seg.status == SegmentStatus.REVIEWED)")],
  ['tests/test_web.py::TestQueuedEdits::test_queued_reviewed_segment_is_translated_when_asked'])

undetected = 0
for name, edits, tests in R:
    if only and not any(name.startswith(o) for o in only):
        continue
    d = tempfile.mkdtemp()
    try:
        subprocess.run(f'git -C {REPO} archive HEAD | tar -x -C {d}', shell=True, check=True)
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
                               cwd=d, capture_output=True, text=True, timeout=600)
            last = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-200:]
            ok = p.returncode == 1 and ' failed' in last
            undetected += not ok
            print(f'{"OK  " if ok else "MISS"} {name}: {last}', flush=True)
    finally:
        shutil.rmtree(d)
print(f'reverts not detected: {undetected}')
sys.exit(1 if undetected else 0)
