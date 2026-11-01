"""课内带练复用原生智能体循环；执行仅指向本课隔离运行器。"""
from __future__ import annotations

import asyncio
import copy
import difflib
import json
import threading
import time
import uuid

from harness.core.agent import AgentLoop
from harness.core.events import EventBus
from harness.core.cleanup import join_cleanup, preserve_cleanup_errors
from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.types import TextBlock, ToolSchema
from harness.prompt.registry import SystemPromptRegistry
from harness.session.store import SessionStore
from harness.tools.registry import ToolDefinition, ToolRegistry, ToolResult
from harness.tools.schema import validate

from .errors import AcademyError
from .files import fingerprint, read_files, version
from .protocol import SCHEMAS


class AcademyTutor:
    """持久保存意图与工具证据，观察取消、重启和未知结果都不续跑。"""

    def __init__(self, service):
        self.service = service
        self.store = service.store
        self._active = {}
        self._drafts = {}
        self._scheduled = {}
        self._stopping = set()
        self._lock = threading.RLock()

    def _stream(self, identity, session_id, event, payload):
        """仅暂存所属请求的可见正文；不公开思考内容，也不为每个增量写盘。"""
        if payload.get('session_id') != session_id:
            return
        with self._lock:
            owner = self._active.get(identity)
            if owner is None or not owner[1] or identity in self._stopping:
                return
            if event == 'request/start':
                self._drafts[identity] = (payload['request_id'], '')
            elif event == 'assistant/delta' and payload.get('type') == 'text':
                previous = self._drafts.get(identity)
                if previous is None or previous[0] != payload.get('request_id') or not isinstance(payload.get('delta'), str):
                    return
                text = (previous[1] + payload['delta'][:16000])[:16000]
                try:
                    text.encode('utf-8')
                except UnicodeError:
                    return
                self._drafts[identity] = (previous[0], text)
            elif event == 'assistant/message' and payload.get('tool_calls'):
                self._drafts.pop(identity, None)

    @staticmethod
    def _kind(project_id):
        return 'tutor-turn:' + project_id

    def _save(self, value):
        self.store.put(self._kind(value['project_id']), value)

    @staticmethod
    def _proposal_diff(path, before, after):
        """展示差异从精确候选正文生成，不能与实际草稿内容脱节。"""
        return '\n'.join(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile='a/' + path, tofile='b/' + path, lineterm='')) + '\n'

    def _view(self, value):
        public = copy.deepcopy({key: item for key, item in value.items() if key != 'snapshot'})
        if validate(public, SCHEMAS['AcademyTutorTurn']):
            raise AcademyError('store-invalid')
        try:
            json.dumps(public, ensure_ascii=False).encode('utf-8')
        except UnicodeError as exc:
            raise AcademyError('store-invalid') from exc
        if (not public['prompt'].strip() or public['language'] not in {'en', 'zh-CN'}
                or len(public['prompt']) > 4000 or len(public['response'].encode('utf-8')) > 65536
                or len(public.get('partial_response', '').encode('utf-8')) > 65536
                or len(public['actions']) > 6 or any(validate(action, SCHEMAS['AcademyTutorAction']) for action in public['actions'])
                or ('knowledge_check' in public and (validate(public['knowledge_check'], SCHEMAS['AcademyTutorCheckSelection']) or public['allow_execution'] or public['require_execution'] or 'model_run_id' in public or 'evidence_run_id' in public or 'proposal_path' in public))
                or ('comparison_model_run_id' in public and (not public['comparison_model_run_id'] or len(public['comparison_model_run_id']) > 128 or not public.get('model_run_id') or public['comparison_model_run_id'] == public['model_run_id']))
                or ('model_run_id' in public and (not public['model_run_id'] or len(public['model_run_id']) > 128 or public['allow_execution'] or public['require_execution'] or 'evidence_run_id' in public or 'proposal_path' in public))
                or ('evidence_run_id' in public and (not public['evidence_run_id'] or public['allow_execution'] or public['require_execution']))
                or ('comparison_run_id' in public and (not public['comparison_run_id'] or len(public['comparison_run_id']) > 128
                    or not public.get('evidence_run_id') or public['comparison_run_id'] == public['evidence_run_id']))
                or ('proposal_path' in public and (not public['proposal_path'] or len(public['proposal_path']) > 512 or public['allow_execution'] or public['require_execution'] or 'evidence_run_id' in public))
                or (public['status'] == 'completed' and (not public['response'].strip() or 'error_code' in public))
                or (public['status'] in {'completed', 'failed'} and 'completed_at' not in public)
                or (public['status'] in {'failed', 'unknown'} and not public.get('error_code'))):
            raise AcademyError('store-invalid')
        for action in public['actions']:
            proposal = action.get('proposal')
            if proposal is not None and (validate(proposal, SCHEMAS['AcademyTutorProposal'])
                    or action['tool'] != 'propose_edit' or action['status'] != 'completed'
                    or proposal['path'] != public.get('proposal_path') or version(proposal['before']) != proposal['expected_version']
                    or proposal['before'].splitlines() == proposal['after'].splitlines()
                    or max(len(proposal['before'].encode('utf-8')), len(proposal['after'].encode('utf-8'))) > 65536
                    or proposal['diff'] != self._proposal_diff(proposal['path'], proposal['before'], proposal['after'])):
                raise AcademyError('store-invalid')
            if action['tool'] == 'propose_edit' and action['status'] == 'completed' and proposal is None:
                raise AcademyError('store-invalid')
        with self._lock:
            if public['status'] == 'running' and public['id'] not in self._active:
                public.update(status='unknown', error_code='tutor-interrupted')
                for action in public['actions']:
                    if action['status'] == 'running':
                        action['status'] = 'unknown'
            elif public['status'] == 'running':
                if public['id'] in self._stopping:
                    public['stopping'] = True
                draft = self._drafts.get(public['id'])
                if draft and draft[1].strip():
                    public['partial_response'] = draft[1]
        return public

    def turns(self, project_id):
        self.service._record(project_id)
        with self.store.lock:
            values = self.store.list(self._kind(project_id), limit=20, max_bytes=1024 * 1024)
            if any(value.get('project_id') != project_id for value in values):
                raise AcademyError('store-invalid')
            return [self._view(value) for value in values]

    def start(self, project_id, request_id, lesson_id, expected_fingerprint, prompt, allow_execution, *, require_execution=False, source=None, evidence_run_id=None, comparison_run_id=None, proposal_path=None, model_run_id=None, comparison_model_run_id=None, knowledge_check=None, model='', schedule=None):
        if (not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000 or type(allow_execution) is not bool
                or type(require_execution) is not bool or (require_execution and not allow_execution)
                or (source is not None and validate(source, SCHEMAS['AcademyExecutionSource']))
                or (evidence_run_id is not None and (not isinstance(evidence_run_id, str) or not 1 <= len(evidence_run_id) <= 128
                    or allow_execution or require_execution or source is not None))
                or (proposal_path is not None and (not isinstance(proposal_path, str) or not 1 <= len(proposal_path) <= 512
                    or allow_execution or require_execution or evidence_run_id is not None))
                or (comparison_run_id is not None and (not isinstance(comparison_run_id, str) or not 1 <= len(comparison_run_id) <= 128
                    or evidence_run_id is None or comparison_run_id == evidence_run_id))):
            raise AcademyError('invalid-request')
        if knowledge_check is not None and (validate(knowledge_check, SCHEMAS['AcademyTutorCheckSelection'])
                or allow_execution or require_execution or source is not None or evidence_run_id is not None or model_run_id is not None or proposal_path is not None):
            raise AcademyError('invalid-request')
        if model_run_id is not None and (not isinstance(model_run_id, str) or not 1 <= len(model_run_id) <= 128
                or allow_execution or require_execution or source is not None or evidence_run_id is not None or proposal_path is not None):
            raise AcademyError('invalid-request')
        if comparison_model_run_id is not None and (not isinstance(comparison_model_run_id, str) or not 1 <= len(comparison_model_run_id) <= 128
                or model_run_id is None or comparison_model_run_id == model_run_id):
            raise AcademyError('invalid-request')
        try:
            prompt.encode('utf-8')
            if comparison_model_run_id is not None:
                comparison_model_run_id.encode('utf-8')
            if model_run_id is not None:
                model_run_id.encode('utf-8')
            if evidence_run_id is not None:
                evidence_run_id.encode('utf-8')
            if comparison_run_id is not None:
                comparison_run_id.encode('utf-8')
            if proposal_path is not None:
                proposal_path.encode('utf-8')
        except UnicodeError as exc:
            raise AcademyError('invalid-request') from exc

        def create():
            record = self.service._record(project_id)
            self.service._require_course(record)
            if record['active_lesson_id'] != lesson_id:
                raise AcademyError('lesson-not-active')
            with self._lock:
                if project_id in [owner[0] for owner in self._active.values()] or len(self._active) >= 2:
                    raise AcademyError('tutor-busy')
            files = read_files(self.service._root(record))
            if fingerprint(files) != expected_fingerprint:
                raise AcademyError('stale-project')
            if source is not None:
                if source['path'] not in files:
                    raise AcademyError('source-not-found')
                files[source['path']] = source['content']
            lesson = next(item for item in self.service.course(record['course_id'])['lessons'] if item['id'] == lesson_id)
            if require_execution and lesson['mode'] != 'python':
                raise AcademyError('lesson-not-runnable')
            if proposal_path is not None:
                if lesson['mode'] != 'python':
                    raise AcademyError('lesson-not-runnable')
                if proposal_path not in files:
                    raise AcademyError('source-not-found')
                if len(files[proposal_path].encode('utf-8')) > 65536:
                    raise AcademyError('tutor-too-large')
            history = [turn for turn in self.turns(project_id) if turn['lesson_id'] == lesson_id and turn['status'] == 'completed'][:4]
            if evidence_run_id is not None:
                # 直接读取所属证据，不能被其他项目的新运行挤出全局近期窗口。
                evidence = self.store.get('run', evidence_run_id, max_bytes=1024 * 1024)
                if evidence is None:
                    raise AcademyError('run-not-found')
                runs = [evidence]
                if comparison_run_id is not None:
                    comparison = self.store.get('run', comparison_run_id, max_bytes=1024 * 1024)
                    if comparison is None:
                        raise AcademyError('run-not-found')
                    runs.append(comparison)
            else:
                runs = self.store.list('run', limit=40, max_bytes=1024 * 1024)
            if any(validate(run, SCHEMAS['AcademyRun']) or validate(run['result'], SCHEMAS['AcademyRunResult']) for run in runs):
                raise AcademyError('store-invalid')
            if evidence_run_id is not None and any(run['project_id'] != project_id or run['lesson_id'] != lesson_id for run in runs):
                raise AcademyError('run-not-found')
            if comparison_run_id is not None and (comparison['kind'] != evidence['kind']
                    or not self.store.is_earlier('run', comparison_run_id, evidence_run_id)):
                raise AcademyError('invalid-request')
            snapshot = {'lesson': {key: lesson[key] for key in ('id', 'mode', 'title', 'goal', 'reading', 'exercise') if key in lesson},
                        'files': files, 'history': [{'id': turn['id'], 'prompt': turn['prompt'], 'response': turn['response'],
                            'input_fingerprint': turn['input_fingerprint']} for turn in reversed(history)],
                        'runs': [{key: run[key] for key in ('id', 'kind', 'input_fingerprint', 'input_changed', 'result')}
                                 for run in runs if run['project_id'] == project_id and run['lesson_id'] == lesson_id][:2]}
            if knowledge_check is not None:
                check = next((item for item in lesson.get('knowledge_checks', []) if item['id'] == knowledge_check['check_id']), None)
                option = next((item for item in check['options'] if item['id'] == knowledge_check['option_id']), None) if check else None
                if check is None or option is None:
                    raise AcademyError('invalid-request')
                snapshot['knowledge_check'] = {'id': check['id'], 'prompt': check['prompt'],
                                               'selected': {key: option[key] for key in ('id', 'label', 'feedback')}}
            if model_run_id is not None and lesson['mode'] != 'model':
                raise AcademyError('lesson-not-runnable')
            if lesson['mode'] == 'model':
                experiment = next((run for run in self.service.experiments.runs(project_id)
                                   if run['lesson_id'] == lesson_id and run['status'] == 'completed'), None) if model_run_id is None else None
                if model_run_id is not None:
                    selected = self.store.get('model-run', model_run_id, max_bytes=1024 * 1024)
                    if selected is None:
                        raise AcademyError('run-not-found')
                    experiment = self.service.experiments._view(selected)
                    if experiment['project_id'] != project_id or experiment['lesson_id'] != lesson_id or experiment['status'] != 'completed':
                        raise AcademyError('run-not-found')
                if experiment is not None:
                    snapshot['model_run'] = experiment
            if comparison_model_run_id is not None:
                comparison = self.store.get('model-run', comparison_model_run_id, max_bytes=1024 * 1024)
                if comparison is None:
                    raise AcademyError('run-not-found')
                comparison = self.service.experiments._view(comparison)
                if comparison['project_id'] != project_id or comparison['lesson_id'] != lesson_id or comparison['status'] != 'completed':
                    raise AcademyError('run-not-found')
                if not self.store.is_earlier('model-run', comparison_model_run_id, model_run_id):
                    raise AcademyError('invalid-request')
                snapshot['comparison_model_run'] = comparison
            if history and not any(item is not None for item in (evidence_run_id, model_run_id, knowledge_check, proposal_path)):
                context = self._history_context(history[0], project_id, lesson_id)
                if context is not None:
                    snapshot['history'][-1]['context'] = context
                    if context.get('model_run') == snapshot.get('model_run') and 'model_run' in context:
                        del snapshot['model_run']
                    if context.get('runs'):
                        snapshot['runs'] = [run for run in snapshot['runs'] if run not in context['runs']]
            try:
                size = len(json.dumps(snapshot, ensure_ascii=False).encode('utf-8'))
            except UnicodeError as exc:
                raise AcademyError('store-invalid') from exc
            if size > 96 * 1024:
                raise AcademyError('tutor-too-large')
            saved = None
            if source is not None:
                self.service._write_source(record, source['path'], source['content'], source['expected_version'], expected_fingerprint)
                saved = self.service.source(project_id, source['path'])
            value = {'id': 'tutor_' + uuid.uuid4().hex, 'project_id': project_id, 'lesson_id': lesson_id,
                     'request_id': request_id, 'input_fingerprint': fingerprint(files), 'prompt': prompt,
                     'allow_execution': allow_execution, 'require_execution': require_execution, 'response': '', 'model': model, 'language': current_language.get(),
                     'status': 'running', 'actions': [], 'created_at': time.time(), 'snapshot': snapshot}
            if knowledge_check is not None:
                value['knowledge_check'] = dict(knowledge_check)
            if comparison_model_run_id is not None:
                value['comparison_model_run_id'] = comparison_model_run_id
            if model_run_id is not None:
                value['model_run_id'] = model_run_id
            if evidence_run_id is not None:
                value['evidence_run_id'] = evidence_run_id
            if comparison_run_id is not None:
                value['comparison_run_id'] = comparison_run_id
            if proposal_path is not None:
                value['proposal_path'] = proposal_path
            self.service._mark_started()
            self._save(value)
            if schedule is None:
                value.update(status='failed', error_code='tutor-unavailable', completed_at=time.time())
                self._save(value)
            else:
                with self._lock:
                    self._active[value['id']] = (project_id, False)
                try:
                    future = schedule(value['id'])
                    with self._lock:
                        self._scheduled[value['id']] = future
                    future.add_done_callback(lambda completed: self._scheduled_ended(value['id']))
                except BaseException:
                    self._ended(value['id'])
                    raise
            return {'project': self.service.project(project_id), 'tutor_turn': self._view(value),
                    **({'source': saved} if saved is not None else {})}
        receipt = self.service._change(request_id, 'tutor-turn', project_id,
            {'lesson_id': lesson_id, 'expected_fingerprint': expected_fingerprint, 'prompt': prompt,
             'allow_execution': allow_execution, 'require_execution': require_execution, **({'source': source} if source is not None else {}),
             **({'knowledge_check': knowledge_check} if knowledge_check is not None else {}),
             **({'comparison_model_run_id': comparison_model_run_id} if comparison_model_run_id is not None else {}),
             **({'model_run_id': model_run_id} if model_run_id is not None else {}),
             **({'evidence_run_id': evidence_run_id} if evidence_run_id is not None else {}),
             **({'comparison_run_id': comparison_run_id} if comparison_run_id is not None else {}),
             **({'proposal_path': proposal_path} if proposal_path is not None else {})}, create)
        if receipt['status'] == 'ready':
            turn = receipt['result'].get('tutor_turn')
            if not isinstance(turn, dict):
                raise AcademyError('store-invalid')
            self._view(turn)
            if turn.get('comparison_model_run_id') != comparison_model_run_id or turn.get('knowledge_check') != knowledge_check or turn.get('model_run_id') != model_run_id or turn.get('evidence_run_id') != evidence_run_id or turn.get('comparison_run_id') != comparison_run_id or turn.get('proposal_path') != proposal_path:
                raise AcademyError('store-invalid')
            if any(turn[key] != expected for key, expected in (
                    ('project_id', project_id), ('request_id', request_id), ('lesson_id', lesson_id),
                    ('prompt', prompt), ('allow_execution', allow_execution), ('require_execution', require_execution))):
                raise AcademyError('store-invalid')
        return receipt

    def stop(self, project_id, turn_id, request_id):
        """停止请求先落回执意图；只取消所属调度，不撤销或重放已接受的工具。"""
        def interrupt():
            self.service._record(project_id)
            value = self.store.get(self._kind(project_id), turn_id, max_bytes=1024 * 1024)
            if value is None or value.get('project_id') != project_id:
                raise AcademyError('run-not-found')
            self._view(value)
            with self._lock:
                future = self._scheduled.get(turn_id)
                if value['status'] == 'running' and future is not None and turn_id not in self._stopping:
                    self._stopping.add(turn_id)
                    if isinstance(future, asyncio.Future):
                        future.get_loop().call_soon_threadsafe(future.cancel)
                    else:
                        future.cancel()
            return {'project': self.service.project(project_id), 'tutor_turn': self._view(value)}
        return self.service._change(request_id, 'tutor-stop', project_id, {'turn_id': turn_id}, interrupt)

    def _ended(self, identity):
        with self._lock:
            self._active.pop(identity, None)
            self._drafts.pop(identity, None)
            self._scheduled.pop(identity, None)
            self._stopping.discard(identity)

    def _scheduled_ended(self, identity):
        """调度回执先取消不代表执行已收尾；已认领任务由_finish释放占用。"""
        with self._lock:
            owner = self._active.get(identity)
            if owner is not None and not owner[1]:
                self._active.pop(identity, None)
                self._drafts.pop(identity, None)
                self._scheduled.pop(identity, None)
                self._stopping.discard(identity)

    async def _accept(self, identity):
        """取消认领等待时仍收回线程结果，不遗落已认领的项目占用。"""
        claim = asyncio.create_task(asyncio.to_thread(self._claim, identity))
        try:
            return await join_cleanup(claim)
        except asyncio.CancelledError as primary:
            errors = []
            try:
                value = claim.result()
                value.update(status='unknown', error_code='tutor-interrupted', completed_at=time.time())
                await join_cleanup(asyncio.create_task(asyncio.to_thread(self._finish, value)))
            except BaseException as exc:
                errors.append(exc)
            with preserve_cleanup_errors(errors, primary):
                raise

    def _validate_history_context(self, context, value, identity):
        """历史材料只读且归属本课，不传递此前的执行权限。"""
        base = {'turn_id', 'project_id', 'lesson_id'}
        families = {'model_run', 'knowledge_check', 'runs'}
        if (not isinstance(context, dict) or not base <= set(context)
                or set(context) - base - families - {'evidence_run_id', 'comparison_run_id', 'comparison_model_run'}
                or ('comparison_model_run' in context and 'model_run' not in context)
                or sum(key in context for key in families) != 1
                or not isinstance(identity, str) or not 1 <= len(identity) <= 128 or context['turn_id'] != identity
                or context['project_id'] != value['project_id'] or context['lesson_id'] != value['lesson_id']):
            raise AcademyError('store-invalid')
        if 'model_run' in context:
            if not isinstance(context['model_run'], dict):
                raise AcademyError('store-invalid')
            model = self.service.experiments._view(context['model_run'])
            if model['status'] != 'completed' or model['project_id'] != value['project_id'] or model['lesson_id'] != value['lesson_id']:
                raise AcademyError('store-invalid')
            if 'comparison_model_run' in context:
                if not isinstance(context['comparison_model_run'], dict):
                    raise AcademyError('store-invalid')
                previous = self.service.experiments._view(context['comparison_model_run'])
                if (previous['status'] != 'completed' or previous['project_id'] != value['project_id']
                        or previous['lesson_id'] != value['lesson_id'] or previous['id'] == model['id']):
                    raise AcademyError('store-invalid')
        elif 'knowledge_check' in context:
            check = context['knowledge_check']
            if (not isinstance(check, dict) or set(check) != {'id', 'prompt', 'selected'}
                    or not isinstance(check['id'], str) or not isinstance(check['prompt'], str)
                    or not isinstance(check['selected'], dict) or set(check['selected']) != {'id', 'label', 'feedback'}
                    or any(not isinstance(text, str) for text in check['selected'].values())
                    or validate({'check_id': check['id'], 'option_id': check['selected']['id']}, SCHEMAS['AcademyTutorCheckSelection'])):
                raise AcademyError('store-invalid')
        else:
            runs = context['runs']
            keys = ('id', 'kind', 'input_fingerprint', 'input_changed', 'result')
            schema = {'type': 'object', 'properties': {key: SCHEMAS['AcademyRun']['properties'][key] for key in keys},
                      'required': list(keys), 'additionalProperties': False}
            if (not isinstance(runs, list) or not 1 <= len(runs) <= 2
                    or any(validate(run, schema) or validate(run['result'], SCHEMAS['AcademyRunResult']) for run in runs)
                    or not context.get('evidence_run_id') or context.get('evidence_run_id') != runs[0]['id']
                    or (len(runs) == 2 and (context.get('comparison_run_id') != runs[1]['id'] or runs[0]['kind'] != runs[1]['kind']))
                    or (len(runs) == 1 and 'comparison_run_id' in context)):
                raise AcademyError('store-invalid')
        if 'runs' not in context and ('evidence_run_id' in context or 'comparison_run_id' in context):
            raise AcademyError('store-invalid')

    def _history_context(self, turn, project_id, lesson_id):
        """直接读取最近已完成问答的冻结证据，不重读原实验或重放工具。"""
        original = self.store.get(self._kind(project_id), turn['id'], max_bytes=1024 * 1024)
        if (original is None or original.get('id') != turn['id'] or original.get('project_id') != project_id or original.get('lesson_id') != lesson_id
                or original.get('status') != 'completed'):
            raise AcademyError('store-invalid')
        self._view(original)
        snapshot = self._snapshot(original)
        context = None
        if original.get('knowledge_check'):
            context = {'knowledge_check': snapshot['knowledge_check']}
        elif original.get('evidence_run_id'):
            context = {'runs': snapshot['runs'], 'evidence_run_id': original['evidence_run_id'],
                       **({'comparison_run_id': original['comparison_run_id']} if original.get('comparison_run_id') else {})}
        elif original.get('model_run_id'):
            context = {'model_run': snapshot['model_run'],
                       **({'comparison_model_run': snapshot['comparison_model_run']} if original.get('comparison_model_run_id') else {})}
        elif snapshot['history'] and snapshot['history'][-1].get('context'):
            context = snapshot['history'][-1]['context']
        elif snapshot.get('model_run'):
            context = {'model_run': snapshot['model_run']}
        if context is not None:
            context = copy.deepcopy({**context, 'turn_id': turn['id'], 'project_id': project_id, 'lesson_id': lesson_id})
            self._validate_history_context(context, original, turn['id'])
        return context

    def _snapshot(self, value):
        """校验冻结材料；历史讨论只携带一层证据，不递归复制整个轮次。"""
        snapshot = value.get('snapshot')
        try:
            snapshot_size = len(json.dumps(snapshot, ensure_ascii=False).encode('utf-8'))
        except (UnicodeError, RecursionError) as exc:
            raise AcademyError('store-invalid') from exc
        if (not isinstance(snapshot, dict) or not {'lesson', 'files', 'history', 'runs'} <= set(snapshot)
                or set(snapshot) - {'lesson', 'files', 'history', 'runs', 'model_run', 'knowledge_check', 'comparison_model_run'}
                or snapshot_size > 96 * 1024
                or not isinstance(snapshot['files'], dict) or any(not isinstance(path, str) or not isinstance(content, str) for path, content in snapshot['files'].items())
                or fingerprint(snapshot['files']) != value['input_fingerprint']
                or not isinstance(snapshot['lesson'], dict) or snapshot['lesson'].get('id') != value['lesson_id']
                or snapshot['lesson'].get('mode') not in {'python', 'study', 'model'}
                or not isinstance(snapshot['history'], list) or len(snapshot['history']) > 4 or not isinstance(snapshot['runs'], list)):
            raise AcademyError('store-invalid')
        if 'knowledge_check' in snapshot or 'knowledge_check' in value:
            check, selection = snapshot.get('knowledge_check'), value.get('knowledge_check')
            if (not isinstance(check, dict) or set(check) != {'id', 'prompt', 'selected'} or not isinstance(check['prompt'], str)
                    or validate(selection, SCHEMAS['AcademyTutorCheckSelection']) or check['id'] != selection['check_id']
                    or not isinstance(check['selected'], dict) or set(check['selected']) != {'id', 'label', 'feedback'}
                    or any(not isinstance(text, str) for text in check['selected'].values()) or check['selected']['id'] != selection['option_id']):
                raise AcademyError('store-invalid')
        if 'model_run' in snapshot:
            if not isinstance(snapshot['model_run'], dict):
                raise AcademyError('store-invalid')
            experiment = self.service.experiments._view(snapshot['model_run'])
            if (snapshot['lesson']['mode'] != 'model' or experiment['project_id'] != value['project_id']
                    or experiment['lesson_id'] != value['lesson_id'] or experiment['status'] != 'completed'):
                raise AcademyError('store-invalid')
        if value.get('model_run_id') and snapshot.get('model_run', {}).get('id') != value['model_run_id']:
            raise AcademyError('store-invalid')
        if 'comparison_model_run' in snapshot or value.get('comparison_model_run_id'):
            previous = snapshot.get('comparison_model_run')
            if not isinstance(previous, dict):
                raise AcademyError('store-invalid')
            previous = self.service.experiments._view(previous)
            if (previous['status'] != 'completed' or previous['project_id'] != value['project_id'] or previous['lesson_id'] != value['lesson_id']
                    or previous['id'] != value.get('comparison_model_run_id') or previous['id'] == value.get('model_run_id') or not value.get('model_run_id')):
                raise AcademyError('store-invalid')
        if value.get('evidence_run_id'):
            expected = [value['evidence_run_id']] + ([value['comparison_run_id']] if value.get('comparison_run_id') else [])
            if len(snapshot['runs']) != len(expected) or any(not isinstance(run, dict) or run.get('id') != identity for run, identity in zip(snapshot['runs'], expected)):
                raise AcademyError('store-invalid')
            if value.get('comparison_run_id') and snapshot['runs'][0].get('kind') != snapshot['runs'][1].get('kind'):
                raise AcademyError('store-invalid')
        if value.get('proposal_path') and (snapshot['lesson']['mode'] != 'python' or value['proposal_path'] not in snapshot['files']
                or len(snapshot['files'][value['proposal_path']].encode('utf-8')) > 65536):
            raise AcademyError('store-invalid')
        for index, entry in enumerate(snapshot['history']):
            if (not isinstance(entry, dict) or not {'prompt', 'response', 'input_fingerprint'} <= set(entry)
                    or set(entry) - {'id', 'prompt', 'response', 'input_fingerprint', 'context'}
                    or not isinstance(entry['prompt'], str) or not isinstance(entry['response'], str)
                    or validate(entry['input_fingerprint'], SCHEMAS['AcademyTutorTurn']['properties']['input_fingerprint'])):
                raise AcademyError('store-invalid')
            if 'context' in entry:
                if index != len(snapshot['history']) - 1:
                    raise AcademyError('store-invalid')
                self._validate_history_context(entry['context'], value, entry.get('id'))
        return snapshot

    def _claim(self, identity):
        with self.store.lock:
            with self._lock:
                owner = self._active.get(identity)
            if owner is None:
                raise AcademyError('tutor-interrupted')
            value = self.store.get(self._kind(owner[0]), identity, max_bytes=1024 * 1024)
            if value is None:
                raise AcademyError('run-not-found')
            self._view(value)
            self._snapshot(value)
            with self._lock:
                if self._active.get(identity) != (value['project_id'], False) or value['status'] != 'running':
                    raise AcademyError('tutor-interrupted')
                self._active[identity] = (value['project_id'], True)
            return value

    def _invoke(self, value, name, args):
        """每种执行每次带练最多一次，持久回执先于向模型反馈。"""
        if any(action['status'] in {'running', 'unknown'} for action in value['actions']):
            raise AcademyError('outcome-unknown')
        if name == 'propose_edit':
            if not value.get('proposal_path') or args.get('path') != value['proposal_path']:
                raise AcademyError('invalid-request')
            previous = next((action for action in value['actions'] if action['tool'] == name and action['status'] == 'completed'), None)
            if previous is not None:
                return previous['output']
        elif name != 'inspect_file':
            previous = next((action for action in value['actions'] if action['tool'] == name), None)
            if previous is not None:
                if previous['status'] == 'failed':
                    raise AcademyError(previous['output'])
                return previous['output']
        if len(value['actions']) >= 6:
            raise AcademyError('tutor-limit')
        action = {'tool': name, 'status': 'running', 'output': '', **({'path': args['path']} if name == 'inspect_file' else {})}
        value['actions'].append(action)
        self._save(value)
        try:
            if name == 'inspect_file':
                content = value['snapshot']['files'].get(args['path'])
                if content is None:
                    raise AcademyError('source-not-found')
                output = {'path': args['path'], 'content': content, 'input_fingerprint': value['input_fingerprint']}
            elif name == 'propose_edit':
                before = value['snapshot']['files'][args['path']]
                old, new = args.get('old_text'), args.get('new_text')
                if (not isinstance(old, str) or not isinstance(new, str) or not old or old == new
                        or max(len(old), len(new)) > 4000 or max(len(old.splitlines()), len(new.splitlines())) > 20
                        or before.count(old) != 1):
                    raise AcademyError('invalid-request')
                try:
                    new.encode('utf-8')
                except UnicodeError as exc:
                    raise AcademyError('invalid-request') from exc
                after = before.replace(old, new, 1)
                if before.splitlines() == after.splitlines():
                    raise AcademyError('invalid-request')
                if len(after.encode('utf-8')) > 65536:
                    raise AcademyError('tutor-too-large')
                diff = self._proposal_diff(args['path'], before, after)
                action['proposal'] = {'path': args['path'], 'before': before, 'after': after, 'expected_version': version(before), 'diff': diff}
                output = {'path': args['path'], 'diff': diff, 'draft_only': True}
            else:
                receipt = self.service.execute(value['project_id'], value['id'] + '_' + name,
                    value['lesson_id'], value['input_fingerprint'], check=name == 'check_lesson')
                if receipt['status'] != 'ready':
                    raise AcademyError(receipt['result'].get('error_code', 'outcome-unknown'))
                run = receipt['result']['run']
                action['run_id'] = run['id']
                output = {key: run[key] for key in ('id', 'kind', 'input_fingerprint', 'input_changed', 'result')}
            text = json.dumps(output, ensure_ascii=False)
            if len(text.encode('utf-8')) > 32768:
                text = json.dumps({'preview': text[:4096], 'total_chars': len(text), 'truncated': True}, ensure_ascii=False)
            action.update(status='completed', output=text)
        except AcademyError as exc:
            action.update(status='unknown' if exc.code == 'outcome-unknown' else 'failed', output=exc.code)
            raise
        except BaseException:
            action.update(status='unknown', output='outcome-unknown')
            raise
        finally:
            self._save(value)
        return action['output']

    def _registry(self, value):
        registry = ToolRegistry()
        names = ['inspect_file'] if value['snapshot']['files'] else []
        if value.get('proposal_path'):
            names += ['propose_edit']
        if value['allow_execution'] and value['snapshot']['lesson']['mode'] == 'python':
            names += ['run_example', 'check_lesson']
        for name in names:
            parameters = {'type': 'object', 'properties': {}, 'additionalProperties': False}
            if name == 'inspect_file':
                parameters.update(properties={'path': {'type': 'string', 'enum': list(value['snapshot']['files']), 'description': ''}}, required=['path'])
            elif name == 'propose_edit':
                parameters.update(properties={'path': {'type': 'string', 'enum': [value['proposal_path']], 'description': ''},
                    'old_text': {'type': 'string', 'minLength': 1, 'maxLength': 4000, 'description': ''},
                    'new_text': {'type': 'string', 'maxLength': 4000, 'description': ''}}, required=['path', 'old_text', 'new_text'])
            async def execute(args, ctx, name=name):
                output = await join_cleanup(asyncio.create_task(asyncio.to_thread(self._invoke, value, name, args)))
                return ToolResult(content=[TextBlock(text=output)])
            parameter_keys = {('properties', 'path', 'description'): 'academy.tutor.tool.path'} if name in {'inspect_file', 'propose_edit'} else {}
            if name == 'propose_edit':
                parameter_keys.update({('properties', field, 'description'): 'academy.tutor.tool.' + field for field in ('old_text', 'new_text')})
            schema = ToolSchema(name, '', parameters, description_key='academy.tutor.tool.' + name, parameter_keys=parameter_keys)
            registry.register(ToolDefinition(schema, execute, effect='read' if name in {'inspect_file', 'propose_edit'} else 'execute', concurrency_safe=False))
        return registry

    def _finish(self, value):
        try:
            self._save(value)
        finally:
            self._ended(value['id'])

    async def evaluate(self, identity, adapter, config):
        """模型调度沿用核心循环，课内工具不接入主对话或宿主Shell。"""
        value = await self._accept(identity)
        token = current_language.set(value['language'])
        session = None
        primary = None
        try:
            prompts = SystemPromptRegistry()
            prompts.add_section('academy:tutor', tr('academy.tutor.instructions'), interpolate=False)
            session = SessionStore().create()
            bus = EventBus()
            for event in ('request/start', 'assistant/delta', 'assistant/message'):
                bus.on(event, lambda payload, event=event: self._stream(identity, session.id, event, payload))
            agent = AgentLoop(adapter, self._registry(value), prompts, config=config.merged(max_tokens=1800), max_steps=4, bus=bus)
            agent.language_preference = lambda: value['language']
            agent.reply_max_chars = 16000
            agent.token_budget = 12000
            agent.context_auto_manage = False
            async def require_run(*args):
                if value['require_execution'] and not any(action['tool'] == 'run_example' for action in value['actions']):
                    return tr('academy.tutor.execution_required')
                if value.get('proposal_path') and not any(action.get('proposal') for action in value['actions']):
                    return tr('academy.tutor.proposal_required')
                return None
            agent.stop_guard = require_run
            context = {**value['snapshot'], 'files': list(value['snapshot']['files']),
                       'task': 'academy-tutor', 'input_fingerprint': value['input_fingerprint'],
                       'allow_execution': value['allow_execution'], 'question': value['prompt']}
            if value.get('evidence_run_id'):
                context['evidence_run_id'] = value['evidence_run_id']
            if value.get('comparison_run_id'):
                context['comparison_run_id'] = value['comparison_run_id']
            if value.get('proposal_path'):
                context['proposal_path'] = value['proposal_path']
            async with asyncio.timeout(90) as deadline:
                result = await agent.prompt(session, json.dumps(context, ensure_ascii=False))
            if asyncio.current_task().cancelling():
                raise asyncio.CancelledError
            value['response'] = result.text
            if result.reason == 'completed' and result.text.strip():
                value['status'] = 'completed'
            else:
                value.update(status='unknown' if result.reason == 'aborted' or deadline.expired() else 'failed',
                             error_code='tutor-interrupted' if result.reason == 'aborted' else 'tutor-limit' if result.reason in {'budget', 'max-steps'} else 'tutor-failed')
            if any(action['status'] in {'running', 'unknown'} for action in value['actions']):
                value.update(status='unknown', error_code='outcome-unknown')
            elif value['require_execution'] and not any(action['tool'] == 'run_example' and action.get('run_id') for action in value['actions']):
                value.update(status='failed', error_code='tutor-execution-required')
            elif value.get('proposal_path') and not any(action.get('proposal') for action in value['actions']):
                value.update(status='failed', error_code='tutor-proposal-required')
            models = [event.data.get('model') for event in session.events_of_type('assistant/message') if event.data.get('model')]
            if models:
                value['model'] = models[-1]
        except asyncio.CancelledError as exc:
            primary = exc
            value.update(status='unknown', error_code='tutor-interrupted')
            raise
        except TimeoutError:
            value.update(status='unknown', error_code='tutor-timeout')
        except Exception:
            value.update(status='failed', error_code='tutor-failed')
        finally:
            current_language.reset(token)
            value['completed_at'] = time.time()
            errors = []
            try:
                if session is not None:
                    await join_cleanup(asyncio.create_task(session.close()))
            except BaseException as exc:
                errors.append(exc)
                value.update(status='unknown', error_code='tutor-interrupted')
            try:
                await join_cleanup(asyncio.create_task(asyncio.to_thread(self._finish, value)))
            except BaseException as exc:
                errors.append(exc)
            with preserve_cleanup_errors(errors, primary):
                pass
