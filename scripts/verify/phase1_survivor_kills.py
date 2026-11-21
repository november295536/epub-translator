#!/usr/bin/env python3
"""Phase 1: the survivors of the mutation runs that new tests kill.

Applies each surviving mutant listed below (as reported by
scripts/verify/mutation.sh, by name <group><mutant id>; "r2-" for review
round 2, "r3-" for review round 3) by hand to a copy of the committed code (git archive HEAD, in a
temporary directory removed afterwards) and runs the named tests. The tests
must pass on the unmutated copy first (baseline); a mutant counts as killed
only when pytest then exits 1 with failed tests (not on a collection,
usage or deselection error). The copy is restored after each mutant.
Prints KILLED or SURVIVED per mutant and the number not killed.

Round 1 mutants whose line review round 2 removed (llm25: the empty output
after giving up on the pipes; web144: the worker's unexpected-error log
line) are no longer listed; llm44 and web171-176 use the round 2 text
around the same mutated code.
Round 4 indented the replacement's project creation: r3-web26 and r3-web28
follow it. Round 4's mutation run left no killable survivor.

Usage:
    python3 scripts/verify/phase1_survivor_kills.py [name ...]

Exit code: 0 when every mutant is killed, 1 otherwise.
"""
import os
import shutil
import subprocess
import sys
import tempfile

REPO = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True,
                      check=True, cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')  # no stale bytecode after a restore

M = [  # (name, file, old text, mutated text, tests)
 ('core18','src/core/project.py',"    model: str = ''\n","    model: str = 'XXXX'\n",['tests/test_project.py::TestRound1Exact::test_project_data_model_default_is_empty']),
 ('core19','src/core/project.py',"    model: str = ''\n","    model: str = None\n",['tests/test_project.py::TestRound1Exact::test_project_data_model_default_is_empty']),
 ('core35','src/core/project.py','raise RuntimeError("No project data to save.")\n        with self.mutex:\n            raw','raise RuntimeError("XXNo project data to save.XX")\n        with self.mutex:\n            raw',['tests/test_project.py::TestRound1Exact::test_messages_without_data']),
 ('core46','src/core/project.py',"prefix=f'{self.PROJECT_FILE}.'","prefix=f'XX{self.PROJECT_FILE}.XX'",['tests/test_project.py::TestRound1Exact::test_temporary_file_name']),
 ('core47','src/core/project.py',"suffix='.tmp'","suffix='XX.tmpXX'",['tests/test_project.py::TestRound1Exact::test_temporary_file_name']),
 ('core49','src/core/project.py',"st_mode & 0o777","st_mode | 0o777",['tests/test_project.py::TestRound1Exact::test_file_mode_kept_or_0644']),
 ('core52','src/core/project.py',"mode = 0o644","mode = 421",['tests/test_project.py::TestRound1Exact::test_file_mode_kept_or_0644']),
 ('core58','src/core/project.py','raise RuntimeError("No project loaded.")\n        with self.mutex:\n            seg = self._find(segment_id)\n            seg.status = SegmentStatus.TRANSLATED','raise RuntimeError("XXNo project loaded.XX")\n        with self.mutex:\n            seg = self._find(segment_id)\n            seg.status = SegmentStatus.TRANSLATED',['tests/test_project.py::TestRound1Exact::test_messages_without_data']),
 ('core62','src/core/project.py',"            seg.translated_text = translated_text\n            seg.translated_at = time.strftime('%Y-%m-%d %H:%M:%S')","            seg.translated_text = translated_text\n            seg.translated_at = time.strftime('XX%Y-%m-%d %H:%M:%SXX')",['tests/test_project.py::TestRound1Exact::test_mark_translated_sets_time']),
 ('core63','src/core/project.py',"            seg.translated_text = translated_text\n            seg.translated_at = time.strftime('%Y-%m-%d %H:%M:%S')","            seg.translated_text = translated_text\n            seg.translated_at = None",['tests/test_project.py::TestRound1Exact::test_mark_translated_sets_time']),
 ('core89','src/core/project.py',"            if text:\n                seg.translated_text = text","            if text:\n                seg.translated_text = None",['tests/test_project.py::TestRound1Exact::test_mark_reviewed_with_text']),
 ('core211','src/core/translator.py','"無法寫入失敗紀錄 %s: %s"','"XX無法寫入失敗紀錄 %s: %sXX"',['tests/test_translator.py::TestRound1Exact::test_log_messages_exact']),
 ('core214','src/core/translator.py',"f'未預期的錯誤：{type(error).__name__}: {error}'","f'XX未預期的錯誤：{type(error).__name__}: {error}XX'",['tests/test_translator.py::TestRound1Exact::test_unexpected_error_outcome']),
 ('core215','src/core/translator.py',"    detail = f'未預期的錯誤：{type(error).__name__}: {error}'[:ERROR_MESSAGE_CHARS]","    detail = None",['tests/test_translator.py::TestRound1Exact::test_unexpected_error_outcome']),
 ('core216','src/core/translator.py',"Issue(kind='backend', code='unexpected'","Issue(kind='XXbackendXX', code='unexpected'",['tests/test_translator.py::TestRound1Exact::test_unexpected_error_outcome']),
 ('core217','src/core/translator.py',"code='unexpected', detail=detail, blocking=True","code='XXunexpectedXX', detail=detail, blocking=True",['tests/test_translator.py::TestRound1Exact::test_unexpected_error_outcome']),
 ('core218','src/core/translator.py',"code='unexpected', detail=detail, blocking=True","code='unexpected', detail=detail, blocking=False",['tests/test_translator.py::TestRound1Exact::test_unexpected_error_outcome']),
 ('core229','src/core/translator.py',"os.makedirs(os.path.dirname(path), exist_ok=True)","os.makedirs(os.path.dirname(path), exist_ok=False)",['tests/test_translator.py::TestRound1Exact::test_second_failure_replaces_the_log']),
 ('core248','src/core/translator.py','"無法移除失敗紀錄 %s: %s"','"XX無法移除失敗紀錄 %s: %sXX"',['tests/test_translator.py::TestRound1Exact::test_log_messages_exact']),
 ('core263','src/glossary/term_extractor.py',"            batches.append('\\n\\n'.join(current_batch))","            batches.append('XX\\n\\nXX'.join(current_batch))",['tests/test_glossary.py::TestExtractionStopKeepsTerms::test_batches_joined_with_blank_line']),
 ('core264','src/glossary/term_extractor.py',"        batches.append('\\n\\n'.join(current_batch))\n    return batches","        batches.append('XX\\n\\nXX'.join(current_batch))\n    return batches",['tests/test_glossary.py::TestExtractionStopKeepsTerms::test_batches_joined_with_blank_line']),
 ('llm92','src/llm/claude_cli.py','SHORT_REPLY_CHARS = 300','SHORT_REPLY_CHARS = 301',['tests/test_llm.py::TestRound1Exact::test_short_reply_boundary']),
 ('llm117','src/llm/claude_cli.py','len(stdout) <= SHORT_REPLY_CHARS','len(stdout) < SHORT_REPLY_CHARS',['tests/test_llm.py::TestRound1Exact::test_short_reply_boundary']),
 ('llm17','src/llm/cli.py','KILL_WAIT_SECONDS = 2','KILL_WAIT_SECONDS = None',['tests/test_llm.py::TestRunProcessExceptionalExit::test_kill_wait_is_bounded_when_a_descendant_keeps_the_pipes']),
 ('llm18','src/llm/cli.py','KEPT_OUTPUT_CHARS = 20000','KEPT_OUTPUT_CHARS = 20001',['tests/test_llm.py::TestTimeoutKeepsOutput::test_output_kept_is_bounded']),
 ('llm19','src/llm/cli.py','KEPT_OUTPUT_CHARS = 20000','KEPT_OUTPUT_CHARS = None',['tests/test_llm.py::TestTimeoutKeepsOutput::test_output_kept_is_bounded']),
 ('llm24','src/llm/cli.py',"            if pipe is not None:","            if pipe is  None:",['tests/test_llm.py::TestRunProcessExceptionalExit::test_kill_wait_is_bounded_when_a_descendant_keeps_the_pipes']),
 ('llm44','src/llm/cli.py',"                    raise CancelledError('cancelled')\n                if time.monotonic() >= deadline","                    raise CancelledError('XXcancelledXX')\n                if time.monotonic() >= deadline",['tests/test_llm.py::TestRound1Exact::test_cancel_message_while_running']),
 ('llm50','src/llm/cli.py',"            if cancel is not None and cancel.is_set():\n                raise CancelledError('cancelled')\n            if time","            if cancel is not None and cancel.is_set():\n                raise CancelledError('XXcancelledXX')\n            if time",['tests/test_llm.py::TestRound1Exact::test_cancel_message_after_finish']),
 ('llm130','src/llm/fake.py',"auth_hint = 'fake: sign in again'","auth_hint = 'XXfake: sign in againXX'",['tests/test_llm.py::TestRound1Exact::test_fake_auth_hint']),
 ('llm131','src/llm/fake.py',"auth_hint = 'fake: sign in again'","auth_hint = None",['tests/test_llm.py::TestRound1Exact::test_fake_auth_hint']),
 ('web155','src/web/blueprints/glossary.py','            stopped = None','            stopped = ""',['tests/test_web.py::TestRound1Exact::test_extraction_status_exact']),
 ('web160','src/web/blueprints/glossary.py',"f'提取中斷：{stopped}（已加入先前批次的 {count} 個術語）'","f'XX提取中斷：{stopped}（已加入先前批次的 {count} 個術語）XX'",['tests/test_web.py::TestRound1Exact::test_extraction_status_exact']),
 ('web162','src/web/blueprints/projects.py',"jsonify({'error': '專案正在翻譯中，請先停止翻譯再刪除","jsonify({'XXerrorXX': '專案正在翻譯中，請先停止翻譯再刪除",['tests/test_web.py::TestRound1Exact::test_delete_refused_message_exact']),
 ('web163','src/web/blueprints/projects.py',"'專案正在翻譯中，請先停止翻譯再刪除'","'XX專案正在翻譯中，請先停止翻譯再刪除XX'",['tests/test_web.py::TestRound1Exact::test_delete_refused_message_exact']),
 ('web171','src/web/blueprints/review.py',"        return jsonify({'error': str(e)}), 404\n    return jsonify({'status': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/approve'","        return jsonify({'error': str(e)}), 404\n    return jsonify({'XXstatusXX': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/approve'",['tests/test_web.py::TestRound1Exact::test_review_responses_exact']),
 ('web175','src/web/blueprints/review.py',"        return jsonify({'error': str(e)}), 404\n    return jsonify({'status': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/retranslate'","        return jsonify({'error': str(e)}), 404\n    return jsonify({'status': 'XXokXX', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/retranslate'",['tests/test_web.py::TestRound1Exact::test_review_responses_exact']),
 ('web177','src/web/blueprints/review.py','if s.segment_id == segment_id), None)\n        if not seg:','if s.segment_id != segment_id), None)\n        if not seg:',['tests/test_web.py::TestRound1Exact::test_retranslate_sends_its_own_segment', 'tests/test_web.py::TestRound1Exact::test_review_responses_exact']),
 ('web182','src/web/blueprints/review.py',"not found'}), 404","not found'}), 405",['tests/test_web.py::TestRound1Exact::test_review_responses_exact']),
 ('web204','src/web/blueprints/review.py','"Backend unavailable during retranslation: %s"','"XXBackend unavailable during retranslation: %sXX"',['tests/test_web.py::TestRound1Exact::test_backend_unavailable_retranslate_exact']),
 ('web205','src/web/blueprints/review.py',"return jsonify({'error': str(e)}), 503","return jsonify({'XXerrorXX': str(e)}), 503",['tests/test_web.py::TestRound1Exact::test_backend_unavailable_retranslate_exact']),
 ('web3','src/web/tasks.py',"(AuthError, 'auth_error'),","(AuthError, 'XXauth_errorXX'),",['tests/test_web.py::TestRound1Exact::test_auth_error_event']),
 ('web14','src/web/tasks.py','self._coordinator.is_alive())\n                or any(','self._coordinator.is_alive())\n                and any(',['tests/test_web.py::TestRound1Exact::test_threads_alive_with_any_worker']),
 ('web22','src/web/tasks.py',"if task.state in ('running', 'paused') or task.threads_alive():","if task.state in ('running', 'XXpausedXX') or task.threads_alive():",['tests/test_web.py::TestRound1Exact::test_task_owns_state_by_state_alone']),
 ('web29','src/web/tasks.py',"(task.state in ('running', 'paused')\n                                     or","(task.state not in ('running', 'paused')\n                                     or",['tests/test_web.py::TestRound1Exact::test_task_owns_state_by_state_alone']),
 ('web32','src/web/tasks.py',"(task.state in ('running', 'paused')\n                                     or","(task.state in ('running', 'paused')\n                                     and",['tests/test_web.py::TestRound1Exact::test_task_owns_state_by_state_alone']),
 ('web37','src/web/tasks.py',"raise ValueError('上一個翻譯任務","raise ValueError('XX上一個翻譯任務",['tests/test_web.py::TestRound1Exact::test_exact_messages_and_registration']),
 ('web50','src/web/tasks.py','                    self.tasks[project_name] = task\n                    return task','                    self.tasks[project_name] = None\n                    return task',['tests/test_web.py::TestRound1Exact::test_exact_messages_and_registration']),
 ('web56','src/web/tasks.py','                    args=(task,),\n                    daemon=True,','                    args=(task,),\n                    daemon=False,',['tests/test_web.py::TestRound1Exact::test_exact_messages_and_registration']),
 ('web86','src/web/tasks.py','{", ".join(unsaved)}','{"XX, XX".join(unsaved)}',['tests/test_web.py::TestRound1Exact::test_failure_report_exact', 'tests/test_web.py::TestRound1Exact::test_failure_report_lists_several_ids']),
 ('web88','src/web/tasks.py','"Translation task failed: %s"','"XXTranslation task failed: %sXX"',['tests/test_web.py::TestRound1Exact::test_failure_report_exact', 'tests/test_web.py::TestRound1Exact::test_failure_report_lists_several_ids']),
 ('web93','src/web/tasks.py',"            'error': message,","            'XXerrorXX': message,",['tests/test_web.py::TestRound1Exact::test_failure_report_exact', 'tests/test_web.py::TestRound1Exact::test_failure_report_lists_several_ids']),
 ('web97','src/web/tasks.py','"段落已在排隊期間被編輯，略過: %s"','"XX段落已在排隊期間被編輯，略過: %sXX"',['tests/test_web.py::TestRound1Exact::test_skip_logged']),
 ('web142','src/web/tasks.py','                        segment_id=seg.segment_id, outcome=None, revision=revision))\n                    continue','                        segment_id=seg.segment_id, outcome=None, revision=revision))\n                    break',['tests/test_web.py::TestRound1Exact::test_worker_goes_on_after_a_skip']),
 ('cli7','translate.py',"{', '.join(aliases)}","{'XX, XX'.join(aliases)}",['tests/test_cli.py::TestCLIRound1Exact::test_invalid_model_message']),
 ('cli8','translate.py','f"\'{value}\' is not one of {\', \'.join(aliases)}"','f"XX\'{value}\' is not one of {\', \'.join(aliases)}XX"',['tests/test_cli.py::TestCLIRound1Exact::test_invalid_model_message']),
 ('cli11','translate.py','help="Translation model: one of the backend\'s models"','help="XXTranslation model: one of the backend\'s modelsXX"',['tests/test_cli.py::TestCLIRound1Exact::test_model_help']),
 ('cli56','translate.py','args=(i,), daemon=True)','args=(i,), daemon=False)',['tests/test_cli.py::TestCLIRound1Exact::test_workers_are_daemons']),
 ('cli75','translate.py','                apply(*result_queue.get(timeout=0.5))\n            except queue_mod.Empty:\n                continue','                apply(*result_queue.get(timeout=0.5))\n            except queue_mod.Empty:\n                break',['tests/test_cli.py::TestCLIRound1Exact::test_slow_result_still_applied']),
 ('cli78','translate.py','click.echo("翻譯中斷，等待進行中的段落結束……")','click.echo("XX翻譯中斷，等待進行中的段落結束……XX")',['tests/test_cli.py::TestCLIRound1Exact::test_supervisor_failure_output']),
 ('cli80','translate.py','f"未儲存的結果（','f"XX未儲存的結果（',['tests/test_cli.py::TestCLIRound1Exact::test_supervisor_failure_output']),
 ('cli82','translate.py',"+ ', '.join(unsaved))","+ 'XX, XX'.join(unsaved))",['tests/test_cli.py::TestCLIRound1Exact::test_supervisor_failure_output']),
 ('cli90','translate.py','click.echo(f"提取中斷：{e}")','click.echo(f"XX提取中斷：{e}XX")',['tests/test_cli.py::TestCLIRound1Exact::test_extraction_stop_line']),
 ('scr14','scripts/verify/phase1_load_projects.py',"        return 'equal', ''","        return 'equal', 'XXXX'",['tests/test_verify_scripts.py::TestLoadCheckRoundTripExact']),
 ('scr20','scripts/verify/phase1_load_projects.py',"if 'model' not in on_disk and {","if 'model' not in on_disk or {",['tests/test_verify_scripts.py::TestLoadCheckRoundTripExact']),
 ('scr22','scripts/verify/phase1_load_projects.py',"return 'model added', ''","return 'model added', 'XXXX'",['tests/test_verify_scripts.py::TestLoadCheckRoundTripExact']),
 ('web172','src/web/blueprints/review.py',"        return jsonify({'error': str(e)}), 404\n    return jsonify({'status': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/approve'","        return jsonify({'error': str(e)}), 404\n    return jsonify({'status': 'XXokXX', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/approve'",['tests/test_web.py::TestRound1Exact::test_review_responses_exact']),
 ('web176','src/web/blueprints/review.py',"        return jsonify({'error': str(e)}), 404\n    return jsonify({'status': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/retranslate'","        return jsonify({'error': str(e)}), 404\n    return jsonify({'status': 'ok', 'XXsegment_idXX': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/retranslate'",['tests/test_web.py::TestRound1Exact::test_review_responses_exact']),
 ('web181','src/web/blueprints/review.py',"f'Segment {segment_id} not found'","f'XXSegment {segment_id} not foundXX'",['tests/test_web.py::TestRound1Exact::test_review_responses_exact']),
 ('web30','src/web/tasks.py',"(task.state in ('running', 'paused')\n                                     or","(task.state in ('XXrunningXX', 'paused')\n                                     or",['tests/test_web.py::TestRound1Exact::test_task_owns_state_by_state_alone']),
 ('web87','src/web/tasks.py',"message += f'（{len(unsaved)}","message += f'XX（{len(unsaved)}",['tests/test_web.py::TestRound1Exact::test_failure_report_exact', 'tests/test_web.py::TestRound1Exact::test_failure_report_lists_several_ids']),
 ('web173','src/web/blueprints/review.py',"        return jsonify({'error': str(e)}), 404\n    return jsonify({'status': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/approve'","        return jsonify({'error': str(e)}), 404\n    return jsonify({'status': 'ok', 'XXsegment_idXX': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/approve'",['tests/test_web.py::TestRound1Exact::test_review_responses_exact']),
 ('web174','src/web/blueprints/review.py',"        return jsonify({'error': str(e)}), 404\n    return jsonify({'status': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/retranslate'","        return jsonify({'error': str(e)}), 404\n    return jsonify({'XXstatusXX': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/retranslate'",['tests/test_web.py::TestRound1Exact::test_review_responses_exact']),
 ('web180','src/web/blueprints/review.py',"return jsonify({'error': f'Segment {segment_id} not found'}), 404","return jsonify({'XXerrorXX': f'Segment {segment_id} not found'}), 404",['tests/test_web.py::TestRound1Exact::test_review_responses_exact']),
 ('web31','src/web/tasks.py',"(task.state in ('running', 'paused')\n                                     or","(task.state in ('running', 'XXpausedXX')\n                                     or",['tests/test_web.py::TestRound1Exact::test_task_owns_state_by_state_alone']),
 # Round 1 survivors whose disposition review round 2 corrected (G9, G14)
 ('llm16','src/llm/cli.py','KILL_WAIT_SECONDS = 2','KILL_WAIT_SECONDS = 3',['tests/test_llm.py::TestRound2PartialOutputAfterKill::test_captured_output_kept']),
 ('llm51','src/llm/cli.py','            if time.monotonic() > deadline:','            if time.monotonic() >= deadline:',['tests/test_llm.py::TestRound2DeadlineBoundary::test_finished_exactly_at_deadline']),
 ('r3-core6','src/core/translator.py',"    trace = ''.join(traceback","    trace = 'XXXX'.join(traceback",['tests/test_translator.py::TestRound3UnexpectedFailureLog::test_log_has_type_message_traceback_and_request']),
 ('r3-web23','src/web/blueprints/projects.py',"final_epub_path = os.path.join(project_dir, 'source.epub')","final_epub_path = os.path.join(project_dir, 'XXsource.epubXX')",['tests/test_web.py::TestRound3ReplaceProjectUnderLock::test_replacement_written']),
 ('r3-web26','src/web/blueprints/projects.py',"title=metadata.get('title', '未知書名'),\n                        author","title=metadata.get('XXtitleXX', '未知書名'),\n                        author",['tests/test_web.py::TestRound3ReplaceProjectUnderLock::test_replacement_written']),
 ('r3-web28','src/web/blueprints/projects.py',"author=metadata.get('author', '未知作者'),\n                        segments","author=metadata.get('XXauthorXX', '未知作者'),\n                        segments",['tests/test_web.py::TestRound3ReplaceProjectUnderLock::test_replacement_written']),
 ('r3-web2','src/web/tasks.py',"                raise FileNotFoundError('專案不存在')\n            with ProjectLock","                raise FileNotFoundError('XX專案不存在XX')\n            with ProjectLock",['tests/test_web.py::TestRound3MissingProject::test_set_model_creates_no_lock_file']),
]


def pytest(copy, tests):
    p = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *tests],
                       cwd=copy, capture_output=True, text=True, env=ENV, timeout=900)
    lines = p.stdout.strip().splitlines()
    return p.returncode, (lines[-1] if lines else p.stderr.strip()[-200:])


def main():
    only = sys.argv[1:]
    todo = [m for m in M if not only or m[0] in only]
    bad = 0
    copy = tempfile.mkdtemp(prefix='survivor-kills-')
    try:
        archive = subprocess.run(['git', '-C', REPO, 'archive', 'HEAD'], capture_output=True,
                                 check=True).stdout
        subprocess.run(['tar', '-x', '-C', copy], input=archive, check=True)
        baseline = {}
        for name, f, old, new, tests in todo:
            key = tuple(tests)
            if key not in baseline:
                code, last = pytest(copy, tests)
                baseline[key] = code == 0
                if code != 0:
                    print('BASELINE FAILS', ' '.join(tests), last, flush=True)
            if not baseline[key]:
                print('NO BASELINE', name, flush=True)
                bad += 1
                continue
            path = os.path.join(copy, f)
            with open(path, encoding='utf-8') as fh:
                text = fh.read()
            if text.count(old) != 1:
                print('BAD', name, f'old text found {text.count(old)} times', flush=True)
                bad += 1
                continue
            try:
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(text.replace(old, new))
                code, last = pytest(copy, tests)
            finally:
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(text)
            killed = code == 1 and ' failed' in last
            bad += not killed
            print('KILLED' if killed else 'SURVIVED', name, last[:100], flush=True)
    finally:
        shutil.rmtree(copy, ignore_errors=True)
    print('not killed', bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
