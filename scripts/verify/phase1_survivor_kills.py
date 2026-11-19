#!/usr/bin/env python3
"""Phase 1, review round 1: the survivors of the mutation run that new tests kill.

Applies each surviving mutant listed below (as reported by
scripts/verify/mutation.sh, by name <group><mutant id>) by hand to a copy of
the committed code (git archive HEAD, in a temporary directory removed
afterwards) and runs the given tests; each must fail. Prints KILLED or
SURVIVED per mutant and the number not killed.

Usage:
    python3 scripts/verify/phase1_survivor_kills.py [name ...]

Exit code: 0 when every mutant is killed, 1 otherwise.
"""
import os, shutil, subprocess, sys, tempfile
REPO = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True,
                      check=True, cwd=os.path.dirname(os.path.abspath(__file__))).stdout.strip()
M = [
 ('core18','src/core/project.py',"    model: str = ''\n","    model: str = 'XXXX'\n",'tests/test_project.py'),
 ('core19','src/core/project.py',"    model: str = ''\n","    model: str = None\n",'tests/test_project.py'),
 ('core35','src/core/project.py','raise RuntimeError("No project data to save.")\n        with self.mutex:\n            raw','raise RuntimeError("XXNo project data to save.XX")\n        with self.mutex:\n            raw','tests/test_project.py'),
 ('core46','src/core/project.py',"prefix=f'{self.PROJECT_FILE}.'","prefix=f'XX{self.PROJECT_FILE}.XX'",'tests/test_project.py'),
 ('core47','src/core/project.py',"suffix='.tmp'","suffix='XX.tmpXX'",'tests/test_project.py'),
 ('core49','src/core/project.py',"st_mode & 0o777","st_mode | 0o777",'tests/test_project.py'),
 ('core52','src/core/project.py',"mode = 0o644","mode = 421",'tests/test_project.py'),
 ('core58','src/core/project.py','raise RuntimeError("No project loaded.")\n        with self.mutex:\n            seg = self._find(segment_id)\n            seg.status = SegmentStatus.TRANSLATED','raise RuntimeError("XXNo project loaded.XX")\n        with self.mutex:\n            seg = self._find(segment_id)\n            seg.status = SegmentStatus.TRANSLATED','tests/test_project.py'),
 ('core62','src/core/project.py',"            seg.translated_text = translated_text\n            seg.translated_at = time.strftime('%Y-%m-%d %H:%M:%S')","            seg.translated_text = translated_text\n            seg.translated_at = time.strftime('XX%Y-%m-%d %H:%M:%SXX')",'tests/test_project.py'),
 ('core63','src/core/project.py',"            seg.translated_text = translated_text\n            seg.translated_at = time.strftime('%Y-%m-%d %H:%M:%S')","            seg.translated_text = translated_text\n            seg.translated_at = None",'tests/test_project.py'),
 ('core89','src/core/project.py',"            if text:\n                seg.translated_text = text","            if text:\n                seg.translated_text = None",'tests/test_project.py'),
 ('core211','src/core/translator.py','"無法寫入失敗紀錄 %s: %s"','"XX無法寫入失敗紀錄 %s: %sXX"','tests/test_translator.py'),
 ('core214','src/core/translator.py',"f'未預期的錯誤：{type(error).__name__}: {error}'","f'XX未預期的錯誤：{type(error).__name__}: {error}XX'",'tests/test_translator.py'),
 ('core215','src/core/translator.py',"    detail = f'未預期的錯誤：{type(error).__name__}: {error}'[:ERROR_MESSAGE_CHARS]","    detail = None",'tests/test_translator.py'),
 ('core216','src/core/translator.py',"Issue(kind='backend', code='unexpected'","Issue(kind='XXbackendXX', code='unexpected'",'tests/test_translator.py'),
 ('core217','src/core/translator.py',"code='unexpected', detail=detail, blocking=True","code='XXunexpectedXX', detail=detail, blocking=True",'tests/test_translator.py'),
 ('core218','src/core/translator.py',"code='unexpected', detail=detail, blocking=True","code='unexpected', detail=detail, blocking=False",'tests/test_translator.py'),
 ('core229','src/core/translator.py',"os.makedirs(os.path.dirname(path), exist_ok=True)","os.makedirs(os.path.dirname(path), exist_ok=False)",'tests/test_translator.py'),
 ('core248','src/core/translator.py','"無法移除失敗紀錄 %s: %s"','"XX無法移除失敗紀錄 %s: %sXX"','tests/test_translator.py'),
 ('core263','src/glossary/term_extractor.py',"                batches.append('\\n\\n'.join(current_batch))","                batches.append('XX\\n\\nXX'.join(current_batch))",'tests/test_glossary.py'),
 ('core264','src/glossary/term_extractor.py',"            batches.append('\\n\\n'.join(current_batch))\n\n","            batches.append('XX\\n\\nXX'.join(current_batch))\n\n",'tests/test_glossary.py'),
 ('llm92','src/llm/claude_cli.py','SHORT_REPLY_CHARS = 300','SHORT_REPLY_CHARS = 301','tests/test_llm.py'),
 ('llm117','src/llm/claude_cli.py','len(stdout) <= SHORT_REPLY_CHARS','len(stdout) < SHORT_REPLY_CHARS','tests/test_llm.py'),
 ('llm17','src/llm/cli.py','KILL_WAIT_SECONDS = 2','KILL_WAIT_SECONDS = None','tests/test_llm.py'),
 ('llm18','src/llm/cli.py','KEPT_OUTPUT_CHARS = 20000','KEPT_OUTPUT_CHARS = 20001','tests/test_llm.py'),
 ('llm19','src/llm/cli.py','KEPT_OUTPUT_CHARS = 20000','KEPT_OUTPUT_CHARS = None','tests/test_llm.py'),
 ('llm24','src/llm/cli.py',"            if pipe is not None:","            if pipe is  None:",'tests/test_llm.py'),
 ('llm25','src/llm/cli.py',"        stdout = stderr = ''","        stdout = stderr = 'XXXX'",'tests/test_llm.py'),
 ('llm44','src/llm/cli.py',"                    _kill_group(proc)\n                    raise CancelledError('cancelled')","                    _kill_group(proc)\n                    raise CancelledError('XXcancelledXX')",'tests/test_llm.py'),
 ('llm50','src/llm/cli.py',"            if cancel is not None and cancel.is_set():\n                raise CancelledError('cancelled')\n            if time","            if cancel is not None and cancel.is_set():\n                raise CancelledError('XXcancelledXX')\n            if time",'tests/test_llm.py'),
 ('llm130','src/llm/fake.py',"auth_hint = 'fake: sign in again'","auth_hint = 'XXfake: sign in againXX'",'tests/test_llm.py'),
 ('llm131','src/llm/fake.py',"auth_hint = 'fake: sign in again'","auth_hint = None",'tests/test_llm.py'),
 ('web155','src/web/blueprints/glossary.py','            stopped = None','            stopped = ""','tests/test_web.py::TestRound1Exact'),
 ('web160','src/web/blueprints/glossary.py',"f'提取中斷：{stopped}（已加入先前批次的 {count} 個術語）'","f'XX提取中斷：{stopped}（已加入先前批次的 {count} 個術語）XX'",'tests/test_web.py::TestRound1Exact'),
 ('web162','src/web/blueprints/projects.py',"jsonify({'error': '專案正在翻譯中","jsonify({'XXerrorXX': '專案正在翻譯中",'tests/test_web.py::TestRound1Exact'),
 ('web163','src/web/blueprints/projects.py',"'專案正在翻譯中，請先停止翻譯再刪除'","'XX專案正在翻譯中，請先停止翻譯再刪除XX'",'tests/test_web.py::TestRound1Exact'),
 ('web171','src/web/blueprints/review.py',"    remove_failure_log(_log_dir(pm), segment_id)\n    return jsonify({'status': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/approve'","    remove_failure_log(_log_dir(pm), segment_id)\n    return jsonify({'XXstatusXX': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/approve'",'tests/test_web.py::TestRound1Exact'),
 ('web175','src/web/blueprints/review.py',"    remove_failure_log(_log_dir(pm), segment_id)\n    return jsonify({'status': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/retranslate'","    remove_failure_log(_log_dir(pm), segment_id)\n    return jsonify({'status': 'XXokXX', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/retranslate'",'tests/test_web.py::TestRound1Exact'),
 ('web177','src/web/blueprints/review.py','if s.segment_id == segment_id), None)\n        if not seg:','if s.segment_id != segment_id), None)\n        if not seg:','tests/test_web.py::TestRound1Exact'),
 ('web182','src/web/blueprints/review.py',"not found'}), 404","not found'}), 405",'tests/test_web.py::TestRound1Exact'),
 ('web204','src/web/blueprints/review.py','"Backend unavailable during retranslation: %s"','"XXBackend unavailable during retranslation: %sXX"','tests/test_web.py::TestRound1Exact'),
 ('web205','src/web/blueprints/review.py',"return jsonify({'error': str(e)}), 503","return jsonify({'XXerrorXX': str(e)}), 503",'tests/test_web.py::TestRound1Exact'),
 ('web3','src/web/tasks.py',"(AuthError, 'auth_error'),","(AuthError, 'XXauth_errorXX'),",'tests/test_web.py::TestRound1Exact'),
 ('web14','src/web/tasks.py','self._coordinator.is_alive())\n                or any(','self._coordinator.is_alive())\n                and any(','tests/test_web.py::TestRound1Exact'),
 ('web22','src/web/tasks.py',"if task.state in ('running', 'paused') or task.threads_alive():","if task.state in ('running', 'XXpausedXX') or task.threads_alive():",'tests/test_web.py::TestRound1Exact'),
 ('web29','src/web/tasks.py',"(task.state in ('running', 'paused')\n                                     or","(task.state not in ('running', 'paused')\n                                     or",'tests/test_web.py::TestRound1Exact'),
 ('web32','src/web/tasks.py',"(task.state in ('running', 'paused')\n                                     or","(task.state in ('running', 'paused')\n                                     and",'tests/test_web.py::TestRound1Exact'),
 ('web37','src/web/tasks.py',"raise ValueError('上一個翻譯任務","raise ValueError('XX上一個翻譯任務",'tests/test_web.py::TestRound1Exact'),
 ('web50','src/web/tasks.py','                    self.tasks[project_name] = task\n                    return task','                    self.tasks[project_name] = None\n                    return task','tests/test_web.py::TestRound1Exact'),
 ('web56','src/web/tasks.py','                    args=(task,),\n                    daemon=True,','                    args=(task,),\n                    daemon=False,','tests/test_web.py::TestRound1Exact'),
 ('web86','src/web/tasks.py','{", ".join(unsaved)}','{"XX, XX".join(unsaved)}','tests/test_web.py::TestRound1Exact'),
 ('web88','src/web/tasks.py','"Translation task failed: %s"','"XXTranslation task failed: %sXX"','tests/test_web.py::TestRound1Exact'),
 ('web93','src/web/tasks.py',"            'error': message,","            'XXerrorXX': message,",'tests/test_web.py::TestRound1Exact'),
 ('web97','src/web/tasks.py','"段落已在排隊期間被編輯，略過: %s"','"XX段落已在排隊期間被編輯，略過: %sXX"','tests/test_web.py::TestRound1Exact'),
 ('web142','src/web/tasks.py','                        segment_id=seg.segment_id, outcome=None, revision=revision))\n                    continue','                        segment_id=seg.segment_id, outcome=None, revision=revision))\n                    break','tests/test_web.py::TestRound1Exact'),
 ('web144','src/web/tasks.py','"[%s] 未預期的錯誤: %s"','"XX[%s] 未預期的錯誤: %sXX"','tests/test_web.py::TestRound1Exact'),
 ('cli7','translate.py',"{', '.join(aliases)}","{'XX, XX'.join(aliases)}",'tests/test_cli.py'),
 ('cli8','translate.py','f"\'{value}\' is not one of {\', \'.join(aliases)}"','f"XX\'{value}\' is not one of {\', \'.join(aliases)}XX"','tests/test_cli.py'),
 ('cli11','translate.py','help="Translation model: one of the backend\'s models"','help="XXTranslation model: one of the backend\'s modelsXX"','tests/test_cli.py'),
 ('cli56','translate.py','args=(i,), daemon=True)','args=(i,), daemon=False)','tests/test_cli.py'),
 ('cli75','translate.py','                apply(*result_queue.get(timeout=0.5))\n            except queue_mod.Empty:\n                continue','                apply(*result_queue.get(timeout=0.5))\n            except queue_mod.Empty:\n                break','tests/test_cli.py'),
 ('cli78','translate.py','click.echo("翻譯中斷，等待進行中的段落結束……")','click.echo("XX翻譯中斷，等待進行中的段落結束……XX")','tests/test_cli.py'),
 ('cli80','translate.py','f"未儲存的結果（','f"XX未儲存的結果（','tests/test_cli.py'),
 ('cli82','translate.py',"+ ', '.join(unsaved))","+ 'XX, XX'.join(unsaved))",'tests/test_cli.py'),
 ('cli90','translate.py','click.echo(f"提取中斷：{e}")','click.echo(f"XX提取中斷：{e}XX")','tests/test_cli.py'),
 ('scr14','scripts/verify/phase1_load_projects.py',"        return 'equal', ''","        return 'equal', 'XXXX'",'tests/test_verify_scripts.py'),
 ('scr20','scripts/verify/phase1_load_projects.py',"if 'model' not in on_disk and {","if 'model' not in on_disk or {",'tests/test_verify_scripts.py'),
 ('scr22','scripts/verify/phase1_load_projects.py',"return 'model added', ''","return 'model added', 'XXXX'",'tests/test_verify_scripts.py'),
 ('web172','src/web/blueprints/review.py',"    remove_failure_log(_log_dir(pm), segment_id)\n    return jsonify({'status': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/approve'","    remove_failure_log(_log_dir(pm), segment_id)\n    return jsonify({'status': 'XXokXX', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/approve'",'tests/test_web.py::TestRound1Exact'),
 ('web176','src/web/blueprints/review.py',"    remove_failure_log(_log_dir(pm), segment_id)\n    return jsonify({'status': 'ok', 'segment_id': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/retranslate'","    remove_failure_log(_log_dir(pm), segment_id)\n    return jsonify({'status': 'ok', 'XXsegment_idXX': segment_id})\n\n\n@review_bp.route('/api/projects/<project_name>/segments/<segment_id>/retranslate'",'tests/test_web.py::TestRound1Exact'),
 ('web181','src/web/blueprints/review.py',"f'Segment {segment_id} not found'","f'XXSegment {segment_id} not foundXX'",'tests/test_web.py::TestRound1Exact'),
 ('web30','src/web/tasks.py',"(task.state in ('running', 'paused')\n                                     or","(task.state in ('XXrunningXX', 'paused')\n                                     or",'tests/test_web.py::TestRound1Exact'),
 ('web87','src/web/tasks.py',"message += f'（{len(unsaved)}","message += f'XX（{len(unsaved)}",'tests/test_web.py::TestRound1Exact'),
]
only = sys.argv[1:]
bad = 0
for name, f, old, new, tests in M:
    if only and name not in only: continue
    d = tempfile.mkdtemp()
    subprocess.run(f'git -C {REPO} archive HEAD | tar -x -C {d}', shell=True, check=True)
    p = os.path.join(d, f); s = open(p).read()
    if s.count(old) != 1:
        print('BAD', name, s.count(old)); bad += 1; continue
    open(p, 'w').write(s.replace(old, new))
    r = subprocess.run([sys.executable, '-m', 'pytest', '-x', '-q', '-p', 'no:cacheprovider', tests], cwd=d, capture_output=True, text=True)
    ok = r.returncode != 0
    bad += not ok
    print('KILLED' if ok else 'SURVIVED', name, r.stdout.strip().splitlines()[-1][:100], flush=True)
    shutil.rmtree(d)
print('not killed', bad)
sys.exit(1 if bad else 0)
