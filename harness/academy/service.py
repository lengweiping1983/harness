"""学习业务：以独立项目和可核验输入组成教学闭环。"""
from __future__ import annotations

import difflib
import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from harness.i18n import tr
from harness.core.language import current_language
from harness.tools.schema import validate

from .catalog import AcademyCatalog
from .authoring import AcademyCourseDesigner
from .errors import AcademyError
from .evaluation import MAX_SUBMISSION_BYTES, PROMPT_VERSION, assess_submission
from .experiments import AcademyExperiments
from .files import MAX_FILE_BYTES, MAX_PROJECT_BYTES, file_path, fingerprint, read_files, version
from .runtime import AcademyRuntime
from .protocol import SCHEMAS
from .store import AcademyStore
from .tutor import AcademyTutor


class AcademyService:
    """写入串行化并先保存意图；课程状态不进入核心会话。"""

    def __init__(self, home: Path, catalog_root: Path | None = None):
        self.catalog = AcademyCatalog(catalog_root)
        self.store = AcademyStore(home)
        self._evaluating = set()
        if catalog_root is None:
            self.catalog.additional_root = self.store.root / 'courses'
            self.catalog.additional_root.mkdir(exist_ok=True)
        self.designer = AcademyCourseDesigner(self.store, self.catalog, self._change, self._mark_started)
        self.experiments = AcademyExperiments(self)
        self.tutor = AcademyTutor(self)

    def courses(self):
        return [{key: course[key] for key in ('id', 'version', 'origin', 'title', 'description')} | {'lesson_count': len(course['lessons'])}
                for course in self.catalog.courses()]

    def course(self, course_id):
        return self.catalog.course(course_id)

    def _record(self, project_id):
        record = self.store.get('project', project_id)
        if record is None:
            raise AcademyError('project-not-found')
        if (not re.fullmatch(r'academy_[a-f0-9]{32}', project_id) or
                not isinstance(record.get('revision'), int) or isinstance(record['revision'], bool) or
                not isinstance(record.get('progress'), list) or not isinstance(record.get('course_id'), str)):
            raise AcademyError('store-invalid')
        return record

    def _root(self, record):
        root = self.store.projects_root / record['id']
        if root.is_symlink() or not root.is_dir():
            raise AcademyError('store-invalid')
        return root

    def project(self, project_id):
        record = self._record(project_id)
        return {**record, 'root': str(self._root(record)), 'fingerprint': fingerprint(read_files(self._root(record))),
                'sandbox': AcademyRuntime.backend()}

    def projects(self):
        return [self.project(record['id']) for record in self.store.list('project')]

    def _submission(self, project_id, submission_id):
        self._record(project_id)
        value = self.store.get('submission', submission_id)
        if value is None or value.get('project_id') != project_id:
            raise AcademyError('submission-not-found')
        return value

    def _submission_view(self, value):
        public = {key: item for key, item in value.items() if key not in {'snapshot', 'prompt_version'}}
        # 重启后不重新发起模型请求。只读投影明确区分未确认和已失败。
        if value['status'] == 'evaluating' and value['id'] not in self._evaluating:
            public.update(status='unknown', error_code='assessment-interrupted')
        return public

    def submission(self, project_id, submission_id):
        with self.store.lock:
            return self._submission_view(self._submission(project_id, submission_id))

    def submissions(self, project_id):
        self._record(project_id)
        with self.store.lock:
            return [self._submission_view(value) for value in self.store.list('submission') if value['project_id'] == project_id]

    def submit(self, project_id, request_id, lesson_id, expected_fingerprint, note, *, model='', schedule=None):
        """先冻结代码与说明；模型点评异步完成，既不执行代码也不更改通过状态。"""
        def create():
            record = self._record(project_id)
            self._require_course(record)
            if record['active_lesson_id'] != lesson_id:
                raise AcademyError('lesson-not-active')
            if not isinstance(note, str) or len(note) > 4000:
                raise AcademyError('invalid-request')
            if any(value['id'] in self._evaluating and value['project_id'] == project_id for value in self.store.list('submission')) or len(self._evaluating) >= 2:
                raise AcademyError('assessment-busy')
            files = read_files(self._root(record))
            if fingerprint(files) != expected_fingerprint:
                raise AcademyError('stale-project')
            sources = {'file:' + path: content for path, content in files.items()}
            sources['note'] = note
            for experiment in self.experiments.runs(project_id):
                if (experiment['lesson_id'] == lesson_id and experiment['status'] == 'completed'
                        and experiment['input_fingerprint'] == expected_fingerprint):
                    sources.update({'model:' + experiment['id'] + ':' + key: experiment[key]
                        for key in ('system', 'prompt', 'response', 'model', 'language')})
                    break
            for run in self.runs(project_id)[:3]:
                if run['lesson_id'] == lesson_id and run['input_fingerprint'] == expected_fingerprint and not run['input_changed']:
                    sources['run:' + run['id']] = json.dumps({key: run['result'][key] if key not in {'stdout', 'stderr'} else run['result'][key][:2000]
                        for key in ('status', 'exit_code', 'stdout', 'stderr')}, ensure_ascii=False)
            if len(json.dumps(sources, ensure_ascii=False).encode('utf-8')) > MAX_SUBMISSION_BYTES:
                raise AcademyError('submission-too-large')
            lesson = next(item for item in self.course(record['course_id'])['lessons'] if item['id'] == lesson_id)
            self._require_course(record)
            value = {'id': 'submission_' + uuid.uuid4().hex, 'project_id': project_id, 'lesson_id': lesson_id,
                     'request_id': request_id, 'input_fingerprint': expected_fingerprint, 'created_at': time.time(),
                     'note': note, 'model': model, 'language': current_language.get(), 'status': 'evaluating',
                     'prompt_version': PROMPT_VERSION,
                     'snapshot': {'sources': sources, 'course_version': record['course_version'],
                                  'course_fingerprint': record['course_fingerprint'],
                                  'lesson': {key: lesson[key] for key in ('id', 'order', 'title', 'goal', 'stage', 'exercise', 'rubric') if key in lesson}}}
            self._mark_started()
            self.store.put('submission', value)
            if schedule is None:
                value.update(status='failed', error_code='assessment-unavailable')
                self.store.put('submission', value)
            else:
                self._evaluating.add(value['id'])
                try:
                    future = schedule(value['id'])
                    future.add_done_callback(lambda completed: self._evaluation_ended(value['id']))
                except Exception:
                    self._evaluating.discard(value['id'])
                    value.update(status='failed', error_code='assessment-unavailable')
                    self.store.put('submission', value)
            return {'project': self.project(project_id), 'submission': self._submission_view(value)}
        return self._change(request_id, 'submit', project_id, {'lesson_id': lesson_id, 'expected_fingerprint': expected_fingerprint, 'note': note}, create)

    def _evaluation_ended(self, submission_id):
        # 回调可能在宿主事件循环执行；登记集合不等待文件执行持有的数据库锁。
        self._evaluating.discard(submission_id)

    def _finish_evaluation(self, value):
        with self.store.lock:
            try:
                self.store.put('submission', value)
            finally:
                self._evaluating.discard(value['id'])

    async def evaluate_submission(self, submission_id, adapter, config):
        """一次点评只写回自己的快照；超时、取消和坏输出均不补跑。"""
        value = await asyncio.to_thread(self.store.get, 'submission', submission_id)
        token = current_language.set(value['language'])
        try:
            assessment, model = await assess_submission(value['snapshot'], adapter, config)
            value.update(status='completed', assessment=assessment, model=model)
        except asyncio.CancelledError:
            value.update(status='unknown', error_code='assessment-interrupted')
            raise
        except TimeoutError:
            value.update(status='failed', error_code='assessment-timeout')
        except AcademyError as exc:
            value.update(status='failed', error_code=exc.code)
        except Exception:
            # 供应商错误可能包含请求或凭据，不向浏览器转发原始诊断。
            value.update(status='failed', error_code='assessment-failed')
        finally:
            current_language.reset(token)
            await asyncio.shield(asyncio.to_thread(self._finish_evaluation, value))

    def _change(self, request_id, operation, scope, args, action):
        digest = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        with self.store.lock:
            cached = self.store.begin(request_id, operation, scope, digest)
            if cached is not None:
                return cached
            self._write_started = False
            try:
                result = action()
            except AcademyError as exc:
                if self._write_started:
                    raise RuntimeError('academy/outcome-unknown') from exc
                return self.store.finish(request_id, {'error_code': exc.code}, failed=True)
            # 非领域异常保留 unknown，调用方只能回查，不能重放。
            return self.store.finish(request_id, result)

    def _mark_started(self):
        self._write_started = True

    def create_project(self, request_id, course_id, name=None):
        def create():
            course = self.course(course_id)
            identity = 'academy_' + uuid.uuid4().hex
            root = self.store.projects_root / identity
            self._mark_started()
            root.mkdir()
            record = {'id': identity, 'course_id': course_id, 'course_version': course['version'],
                      'name': name or tr('ui.academy.defaultProject'), 'revision': 0,
                      'course_fingerprint': self.catalog.pin(course_id),
                      'active_lesson_id': course['lessons'][0]['id'], 'created_at': time.time(), 'progress': []}
            self.store.put('project', record)
            self._load_lesson(record, course['lessons'][0]['id'])
            return {'project': self.project(identity)}
        return self._change(request_id, 'create', course_id, {'course_id': course_id, 'name': name}, create)

    def _require_course(self, record):
        if record['course_fingerprint'] != self.catalog.pin(record['course_id']):
            raise AcademyError('course-changed')

    def _validate_input(self, record, expected_revision, expected_fingerprint):
        if record['revision'] != expected_revision or fingerprint(read_files(self._root(record))) != expected_fingerprint:
            raise AcademyError('stale-project')

    def _save(self, record):
        self._mark_started()
        record['revision'] += 1
        self.store.put('project', record)

    def _checkpoint(self, record, lesson_id, kind='manual'):
        files = read_files(self._root(record))
        checkpoint = {'id': 'checkpoint_' + uuid.uuid4().hex, 'project_id': record['id'], 'lesson_id': lesson_id,
                      'kind': kind, 'created_at': time.time(), 'fingerprint': fingerprint(files), 'files': files}
        self._mark_started()
        self.store.put('checkpoint', checkpoint)
        return {key: value for key, value in checkpoint.items() if key != 'files'}

    def _preflight_replace(self, root, files):
        targets = {}
        for raw, content in files.items():
            path = file_path(root, raw)
            temp = path.with_name(path.name + '.academy-tmp')
            if temp.exists() or temp.is_symlink() or any(parent.exists() and not parent.is_dir()
                    for parent in path.parents if parent.is_relative_to(root)):
                raise AcademyError('restore-conflict')
            targets[raw] = (path, temp, content)
        return targets

    def _replace(self, root, files):
        current = read_files(root)
        targets = self._preflight_replace(root, files)
        changed = False
        try:
            for path, temp, content in targets.values():
                changed = True
                self._mark_started()
                path.parent.mkdir(parents=True, exist_ok=True)
                temp.write_text(content, encoding='utf-8')
                os.replace(temp, path)
            for raw in current.keys() - files.keys():
                changed = True
                self._mark_started()
                file_path(root, raw).unlink()
        except Exception as exc:
            if changed:
                # 已经开始文件写入，后续失败不能写成明确未执行的失败回执。
                raise RuntimeError('academy/outcome-unknown') from exc
            raise

    def _load_lesson(self, record, lesson_id):
        self._replace(self._root(record), self.catalog.starter(record['course_id'], lesson_id))
        checkpoint = self._checkpoint(record, lesson_id, 'lesson')
        previous = next((item for item in record['progress'] if item['lesson_id'] == lesson_id), {})
        progress = {'lesson_id': lesson_id, 'state': 'in_progress', 'checkpoint_id': checkpoint['id'],
                    'evidence_ids': [], 'attempt_count': previous.get('attempt_count', 0)}
        record['progress'] = [item for item in record['progress'] if item['lesson_id'] != lesson_id] + [progress]
        record['active_lesson_id'] = lesson_id
        self._save(record)
        return progress

    def start_lesson(self, project_id, request_id, lesson_id, expected_revision, expected_fingerprint):
        def start():
            record = self._record(project_id)
            self._require_course(record)
            self._validate_input(record, expected_revision, expected_fingerprint)
            self.catalog.lesson(record['course_id'], lesson_id)
            if record['active_lesson_id'] == lesson_id:
                return {'project': self.project(project_id)}
            self._checkpoint(record, record['active_lesson_id'], 'before-switch')
            self._load_lesson(record, lesson_id)
            return {'project': self.project(project_id)}
        return self._change(request_id, 'start', project_id, locals_args(lesson_id, expected_revision, expected_fingerprint), start)

    def files(self, project_id):
        record = self._record(project_id)
        return [{'path': path, 'version': version(content)} for path, content in read_files(self._root(record)).items()]

    def source(self, project_id, raw_path):
        record = self._record(project_id)
        path = file_path(self._root(record), raw_path)
        files = read_files(self._root(record))
        if raw_path not in files or not path.is_file():
            raise AcademyError('source-not-found')
        return {'path': raw_path, 'content': files[raw_path], 'version': version(files[raw_path])}

    def write_file(self, project_id, request_id, path, content, expected_version, expected_fingerprint):
        def write():
            record = self._record(project_id)
            self._write_source(record, path, content, expected_version, expected_fingerprint)
            return {'project': self.project(project_id), 'source': self.source(project_id, path)}
        return self._change(request_id, 'write', project_id, {'path': path, 'content': content, 'version': expected_version,
                            'fingerprint': expected_fingerprint}, write)

    def _write_source(self, record, path, content, expected_version, expected_fingerprint):
        """保存共用版本检查；执行前保存不另建可被重放的子请求。"""
        files = read_files(self._root(record))
        if fingerprint(files) != expected_fingerprint or version(files.get(path)) != expected_version:
            raise AcademyError('stale-file')
        if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_FILE_BYTES:
            raise AcademyError('invalid-file')
        file_path(self._root(record), path)
        files[path] = content
        if sum(len(value.encode('utf-8')) for value in files.values()) > MAX_PROJECT_BYTES:
            raise AcademyError('project-limit')
        self._replace(self._root(record), files)
        for progress in record['progress']:
            if progress['lesson_id'] == record['active_lesson_id']:
                progress.update(state='in_progress', evidence_ids=[])
        self._save(record)

    def execute(self, project_id, request_id, lesson_id, expected_fingerprint, *, check=False, source=None):
        if source is not None:
            if validate(source, SCHEMAS['AcademyExecutionSource']):
                raise AcademyError('invalid-request')
        def execute():
            record = self._record(project_id)
            self._require_course(record)
            if lesson_id != record['active_lesson_id']:
                raise AcademyError('lesson-not-active')
            files = read_files(self._root(record))
            if fingerprint(files) != expected_fingerprint:
                raise AcademyError('stale-project')
            lesson = self.catalog.lesson(record['course_id'], lesson_id)
            if lesson.get('mode', 'python') != 'python':
                raise AcademyError('lesson-not-runnable')
            saved = None
            if source is not None:
                if source['path'] not in files:
                    raise AcademyError('source-not-found')
                self._write_source(record, source['path'], source['content'], source['expected_version'], expected_fingerprint)
                saved = self.source(project_id, source['path'])
            input_fingerprint = fingerprint(read_files(self._root(record)))
            run_id = 'run_' + uuid.uuid4().hex
            result = AcademyRuntime(self._root(record)).run(lesson['run_command'],
                         check_script=self.catalog.checker(record['course_id'], lesson_id) if check else None,
                         on_started=self._mark_started)
            input_changed = fingerprint(read_files(self._root(record))) != input_fingerprint
            events = [{'seq': 1, 'type': 'execution/start', 'source': 'host'},
                      {'seq': 2, 'type': 'execution/result', 'source': 'host'}]
            # 课程模拟轨迹与宿主执行事实分别标注，不补造模型思考或真实模型调用。
            if not check and not result['truncated']:
                try:
                    body = json.loads(result['stdout'])
                    for event in body.get('events', [])[:64]:
                        if isinstance(event, str) and len(event) <= 64:
                            events.insert(-1, {'seq': len(events), 'type': event, 'source': 'fixture'})
                except (ValueError, AttributeError, TypeError):
                    pass
            for index, event in enumerate(events, 1):
                event['seq'] = index
            run = {'id': run_id, 'project_id': project_id, 'lesson_id': lesson_id, 'request_id': request_id,
                   'input_fingerprint': input_fingerprint, 'input_changed': input_changed, 'kind': 'check' if check else 'run',
                   'created_at': time.time(), 'result': result, 'events': events}
            self.store.put('run', run)
            if check:
                progress = next(item for item in record['progress'] if item['lesson_id'] == lesson_id)
                progress.update(state='passed' if result['status'] == 'passed' and not input_changed else 'in_progress',
                                attempt_count=progress['attempt_count'] + 1, evidence_ids=[] if input_changed else [run_id])
                self._save(record)
            return {'project': self.project(project_id), 'run': run, 'passed': result['status'] == 'passed' and not input_changed,
                    **({'source': saved} if saved is not None else {})}
        return self._change(request_id, 'check' if check else 'run', project_id,
                            {'lesson_id': lesson_id, 'fingerprint': expected_fingerprint, **({'source': source} if source is not None else {})}, execute)

    def complete_study(self, project_id, request_id, lesson_id, expected_fingerprint, note, answers):
        """自测和反思完成只记录学习活动，不生成代码检查或能力认证。"""
        def complete():
            record = self._record(project_id)
            self._require_course(record)
            if record['active_lesson_id'] != lesson_id:
                raise AcademyError('lesson-not-active')
            lesson = next(item for item in self.course(record['course_id'])['lessons'] if item['id'] == lesson_id)
            if lesson['mode'] not in ('study', 'model') or not isinstance(note, str) or not note.strip() or len(note) > 4000:
                raise AcademyError('invalid-request')
            if fingerprint(read_files(self._root(record))) != expected_fingerprint:
                raise AcademyError('stale-project')
            if (not isinstance(answers, list) or any(not isinstance(item, dict) or set(item) != {'check_id', 'option_id'}
                    or not isinstance(item['check_id'], str) or not isinstance(item['option_id'], str) for item in answers)):
                raise AcademyError('invalid-request')
            expected = {check['id']: check['answer_id'] for check in lesson.get('knowledge_checks', [])}
            actual = {answer['check_id']: answer['option_id'] for answer in answers}
            if len(actual) != len(answers) or actual != expected:
                raise AcademyError('study-incomplete')
            experiment = None
            if lesson['mode'] == 'model':
                experiment = next((run for run in self.experiments.runs(project_id)
                    if run['lesson_id'] == lesson_id and run['input_fingerprint'] == expected_fingerprint
                    and run['status'] == 'completed'), None)
                if experiment is None:
                    raise AcademyError('model-incomplete')
            progress = next(item for item in record['progress'] if item['lesson_id'] == lesson_id)
            if progress['state'] == 'completed':
                return {'project': self.project(project_id)}
            evidence = {'id': 'study_' + uuid.uuid4().hex, 'project_id': project_id, 'lesson_id': lesson_id,
                        'request_id': request_id, 'input_fingerprint': expected_fingerprint, 'note': note,
                        'answers': answers, 'created_at': time.time()}
            if experiment is not None:
                evidence['model_run_id'] = experiment['id']
            self._mark_started()
            self.store.put('study-completion', evidence)
            progress.update(state='completed', evidence_ids=[evidence['id']], attempt_count=progress['attempt_count'] + 1)
            self._save(record)
            return {'project': self.project(project_id)}
        return self._change(request_id, 'complete-study', project_id,
                            {'lesson_id': lesson_id, 'expected_fingerprint': expected_fingerprint, 'note': note, 'answers': answers}, complete)

    def runs(self, project_id):
        self._record(project_id)
        return [record for record in self.store.list('run') if record['project_id'] == project_id]

    def trace(self, project_id, run_id):
        record = self.store.get('run', run_id)
        if record is None or record['project_id'] != project_id:
            raise AcademyError('run-not-found')
        return record['events']

    def checkpoints(self, project_id):
        self._record(project_id)
        return [{key: value for key, value in record.items() if key != 'files'}
                for record in self.store.list('checkpoint') if record['project_id'] == project_id]

    def checkpoint(self, project_id, request_id, expected_fingerprint):
        def save():
            record = self._record(project_id)
            if fingerprint(read_files(self._root(record))) != expected_fingerprint:
                raise AcademyError('stale-project')
            return {'checkpoint': self._checkpoint(record, record['active_lesson_id'])}
        return self._change(request_id, 'checkpoint', project_id, {'fingerprint': expected_fingerprint}, save)

    def restore_preview(self, project_id, checkpoint_id):
        record = self._record(project_id)
        checkpoint = self.store.get('checkpoint', checkpoint_id)
        if checkpoint is None or checkpoint.get('project_id') != project_id:
            raise AcademyError('checkpoint-not-found')
        current = read_files(self._root(record))
        desired = checkpoint.get('files')
        if not isinstance(desired, dict) or fingerprint(desired) != checkpoint['fingerprint']:
            raise AcademyError('store-invalid')
        patches = []
        for path in sorted(current.keys() | desired.keys()):
            if current.get(path) != desired.get(path):
                patch = ''.join(difflib.unified_diff(current.get(path, '').splitlines(keepends=True),
                                    desired.get(path, '').splitlines(keepends=True), fromfile=path, tofile=path))
                patches.append({'path': path, 'diff': patch[:16000]})
        return {'project_id': project_id, 'checkpoint_id': checkpoint_id, 'fingerprint': fingerprint(current), 'changes': patches}

    def restore(self, project_id, request_id, checkpoint_id, expected_fingerprint):
        def restore():
            record = self._record(project_id)
            preview = self.restore_preview(project_id, checkpoint_id)
            if preview['fingerprint'] != expected_fingerprint:
                raise AcademyError('restore-conflict')
            checkpoint = self.store.get('checkpoint', checkpoint_id)
            self._preflight_replace(self._root(record), checkpoint['files'])
            backup = self._checkpoint(record, record['active_lesson_id'], 'before-restore')
            self._replace(self._root(record), checkpoint['files'])
            record['active_lesson_id'] = checkpoint['lesson_id']
            for item in record['progress']:
                if item['lesson_id'] == checkpoint['lesson_id']:
                    item.update(state='in_progress', evidence_ids=[])
            self._save(record)
            return {'project': self.project(project_id), 'backup_checkpoint_id': backup['id']}
        return self._change(request_id, 'restore', project_id,
                            {'checkpoint_id': checkpoint_id, 'fingerprint': expected_fingerprint}, restore)

    def receipt(self, request_id):
        return self.store.receipt(request_id)


def locals_args(lesson_id, revision, fingerprint_value):
    return {'lesson_id': lesson_id, 'revision': revision, 'fingerprint': fingerprint_value}
