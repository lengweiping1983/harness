"""模型实验：冻结输入、单次调用和可回查证据，不执行模型生成的代码。"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid

from harness.core.cleanup import join_cleanup, preserve_cleanup_errors
from harness.core.language import current_language
from harness.llm.types import SystemMessage, UserMessage
from harness.tools.schema import validate

from .errors import AcademyError
from .files import fingerprint, read_files
from .protocol import SCHEMAS


class AcademyExperiments:
    """实验任务由宿主拥有；观察取消和服务重启均不重放模型请求。"""

    def __init__(self, service):
        self.service = service
        self.store = service.store
        self._active = {}
        self._drafts = {}
        self._scheduled = {}
        self._stopping = set()
        self._lock = threading.RLock()

    def _stream(self, identity, event):
        """只投影所属调用的正文，思考与临时文字都不进入持久证据。"""
        if event.kind != 'text' or not isinstance(event.text, str):
            return
        try:
            event.text.encode('utf-8')
        except UnicodeError:
            return
        with self._lock:
            owner = self._active.get(identity)
            if owner is not None and owner[1] and identity not in self._stopping:
                self._drafts[identity] = (self._drafts.get(identity, '') + event.text)[:16000]

    def _view(self, value, *, projected=False):
        public = dict(value)
        try:
            for key in ('system', 'prompt', 'response', 'partial_response'):
                if isinstance(value.get(key), str):
                    value[key].encode('utf-8')
        except UnicodeError as exc:
            raise AcademyError('store-invalid') from exc
        if (validate(public, SCHEMAS['AcademyModelRun']) or ('partial_response' in value and (not projected or value['status'] != 'running' or len(value['partial_response']) > 16000))
                or ('stopping' in value and (not projected or value['status'] != 'running')) or not isinstance(value.get('system'), str)
                or not value['system'].strip() or not public['prompt'].strip()
                or len(public['system']) > 8000 or len(public['prompt']) > 8000 or len(public['response'].encode('utf-8')) > 64 * 1024
                or (public['status'] == 'running' and (public['response'] or 'error_code' in public or 'completed_at' in public))
                or (public['status'] in ('completed', 'failed') and 'completed_at' not in public)
                or (public['status'] == 'completed' and (not public['response'].strip() or 'error_code' in public))
                or (public['status'] in ('failed', 'unknown') and not public.get('error_code'))):
            raise AcademyError('store-invalid')
        with self._lock:
            public.pop('partial_response', None)
            public.pop('stopping', None)
            if public['status'] == 'running' and public['id'] not in self._active:
                public.update(status='unknown', error_code='model-interrupted')
            if public['status'] == 'running' and public['id'] in self._stopping:
                public['stopping'] = True
            if public['status'] == 'running' and not public.get('stopping') and self._drafts.get(public['id']):
                public['partial_response'] = self._drafts[public['id']]
        return public

    def runs(self, project_id):
        self.service._record(project_id)
        with self.store.lock:
            return [self._view(value) for value in self.store.list('model-run') if value.get('project_id') == project_id]

    def start(self, project_id, request_id, lesson_id, expected_fingerprint, prompt, *, model='', schedule=None):
        """先保存完整实验意图，再由宿主安排唯一的模型调用。"""
        if isinstance(prompt, str):
            try:
                prompt.encode('utf-8')
            except UnicodeError as exc:
                raise AcademyError('invalid-request') from exc
        def create():
            record = self.service._record(project_id)
            self.service._require_course(record)
            if record['active_lesson_id'] != lesson_id:
                raise AcademyError('lesson-not-active')
            lesson = next(item for item in self.service.course(record['course_id'])['lessons'] if item['id'] == lesson_id)
            if lesson['mode'] != 'model' or not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000:
                raise AcademyError('invalid-request')
            if fingerprint(read_files(self.service._root(record))) != expected_fingerprint:
                raise AcademyError('stale-project')
            with self._lock:
                if any(owner[0] == project_id for owner in self._active.values()) or len(self._active) >= 2:
                    raise AcademyError('model-busy')
            value = {'id': 'model_' + uuid.uuid4().hex, 'project_id': project_id, 'lesson_id': lesson_id,
                     'request_id': request_id, 'input_fingerprint': expected_fingerprint, 'prompt': prompt,
                     'system': lesson['model_system'], 'response': '', 'model': model,
                     'language': current_language.get(), 'status': 'running', 'created_at': time.time()}
            self.service._require_course(record)
            self.service._mark_started()
            self.store.put('model-run', value)
            if schedule is None:
                value.update(status='failed', error_code='model-unavailable', completed_at=time.time())
                self.store.put('model-run', value)
            else:
                with self._lock:
                    self._active[value['id']] = (project_id, False)
                try:
                    future = schedule(value['id'])
                    with self._lock:
                        self._scheduled[value['id']] = future
                    future.add_done_callback(lambda completed: self._scheduled_ended(value['id']))
                except BaseException:
                    # 只撤销未确认的内存登记；原意图保持未知，不补跑模型。
                    self._ended(value['id'])
                    raise
            return {'project': self.service.project(project_id), 'model_run': self._view(value)}
        receipt = self.service._change(request_id, 'model-run', project_id,
            {'lesson_id': lesson_id, 'expected_fingerprint': expected_fingerprint, 'prompt': prompt}, create)
        if receipt['status'] == 'ready':
            run = receipt['result'].get('model_run')
            if not isinstance(run, dict):
                raise AcademyError('store-invalid')
            self._view(run, projected=True)
            if any(run[key] != expected for key, expected in (
                    ('project_id', project_id), ('request_id', request_id), ('lesson_id', lesson_id),
                    ('input_fingerprint', expected_fingerprint), ('prompt', prompt))):
                raise AcademyError('store-invalid')
        return receipt

    def stop(self, project_id, run_id, request_id):
        """停止请求先落回执意图；只取消所属模型调用，不撤销或重放已接受的请求。"""
        def interrupt():
            self.service._record(project_id)
            value = self.store.get('model-run', run_id, max_bytes=1024 * 1024)
            if value is None or value.get('project_id') != project_id:
                raise AcademyError('run-not-found')
            self._view(value)
            with self._lock:
                future = self._scheduled.get(run_id)
                if value['status'] == 'running' and future is not None and run_id not in self._stopping:
                    self._stopping.add(run_id)
                    if isinstance(future, asyncio.Future):
                        future.get_loop().call_soon_threadsafe(future.cancel)
                    else:
                        future.cancel()
            return {'project': self.service.project(project_id), 'model_run': self._view(value)}
        return self.service._change(request_id, 'model-stop', project_id, {'run_id': run_id}, interrupt)

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
                value.update(status='unknown', error_code='model-interrupted', completed_at=time.time())
                await join_cleanup(asyncio.create_task(asyncio.to_thread(self._finish, value)))
            except BaseException as exc:
                errors.append(exc)
            with preserve_cleanup_errors(errors, primary):
                raise

    def _claim(self, identity):
        with self.store.lock:
            value = self.store.get('model-run', identity)
            if value is None:
                raise AcademyError('run-not-found')
            self._view(value)
            with self._lock:
                if self._active.get(identity) != (value['project_id'], False) or value['status'] != 'running':
                    raise AcademyError('model-interrupted')
                # 保留占用到持久收尾结束，重复执行入口不能再次认领。
                self._active[identity] = (value['project_id'], True)
            return value

    def _finish(self, value):
        try:
            self.store.put('model-run', value)
        finally:
            self._ended(value['id'])

    async def evaluate(self, identity, adapter, config):
        """一次实验只发送系统约束和用户提示，不携带对话、工具或历史实验。"""
        value = await self._accept(identity)
        token = current_language.set(value['language'])
        primary = None
        try:
            async with asyncio.timeout(60):
                response = await adapter.complete(
                    [SystemMessage(value['system']), UserMessage(value['prompt'])],
                    config=config.merged(max_tokens=4000), tools=[],
                    on_event=lambda event: self._stream(identity, event),
                )
            if (response.tool_uses or not response.text.strip()
                    or len(response.text.encode('utf-8')) > 64 * 1024):
                raise AcademyError('model-invalid')
            value.update(status='completed', response=response.text, model=response.model or config.model)
            if response.stop_reason in {'length', 'max_tokens'}:
                value.update(status='failed', error_code='model-truncated')
        except asyncio.CancelledError as exc:
            primary = exc
            value.update(status='unknown', error_code='model-interrupted')
            raise
        except TimeoutError:
            value.update(status='unknown', error_code='model-timeout')
        except AcademyError as exc:
            value.update(status='failed', error_code=exc.code)
        except Exception:
            # 不向页面暴露可能包含请求或凭据的供应商原始错误。
            value.update(status='failed', error_code='model-failed')
        finally:
            current_language.reset(token)
            value['completed_at'] = time.time()
            errors = []
            try:
                await join_cleanup(asyncio.create_task(asyncio.to_thread(self._finish, value)))
            except BaseException as exc:
                errors.append(exc)
            with preserve_cleanup_errors(errors, primary):
                pass
