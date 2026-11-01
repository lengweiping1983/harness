"""主题制课：规划、内容编写和质量复核均在独立学习域中完成。"""
from __future__ import annotations

import ast
import asyncio
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid

from harness.core.cleanup import join_cleanup
from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.types import SystemMessage, UserMessage
from harness.tools.schema import validate

from .catalog import AcademyCatalog, IDENTITY
from .errors import AcademyError
from .files import file_path, read_files
from .protocol import BOOL, SCHEMAS, array, obj


PROMPT_VERSION = 'academy-authoring-v1'
LANGUAGES = ('en', 'zh-CN')


def text_schema(limit, minimum=1):
    return {'type': 'string', 'minLength': minimum, 'maxLength': limit}


def localized(limit, minimum=1):
    return obj({language: text_schema(limit, minimum) for language in LANGUAGES})


IDENTIFIER = {'type': 'string', 'pattern': '^' + IDENTITY + '$'}
PLAN_SCHEMA = obj({
    'title': localized(120), 'description': localized(600), 'assumptions': localized(1200),
    'stages': {'type': 'array', 'minItems': 2, 'maxItems': 4, 'items': obj({
        'id': IDENTIFIER, 'title': localized(100), 'goal': localized(500)})},
    'lessons': {'type': 'array', 'minItems': 3, 'maxItems': 8, 'items': obj({
        'id': IDENTIFIER, 'stage_id': IDENTIFIER, 'mode': {'type': 'string', 'enum': ['python', 'study']},
        'title': localized(120), 'goal': localized(500), 'prerequisites': array(IDENTIFIER),
        'duration_minutes': {'type': 'integer', 'minimum': 5, 'maximum': 45}})},
})
CONTENT_SCHEMA = obj({'lessons': {'type': 'array', 'minItems': 3, 'maxItems': 8, 'items': obj({
    'id': IDENTIFIER, 'reading': localized(6000, 80), 'exercise': localized(1600, 20), 'rubric': localized(1200, 20),
    'starter': text_schema(12000, 0), 'checker': text_schema(12000, 0),
    'knowledge_checks': {'type': 'array', 'minItems': 1, 'maxItems': 3, 'items': obj({
        'id': IDENTIFIER, 'prompt': localized(600), 'answer_id': IDENTIFIER,
        'options': {'type': 'array', 'minItems': 2, 'maxItems': 4, 'items': obj({
            'id': IDENTIFIER, 'label': localized(400), 'feedback': localized(1000)})},
        'hints': {'type': 'array', 'minItems': 1, 'maxItems': 3, 'items': localized(800)},
    })},
})}})
REVIEW_SCHEMA = obj({'approved': BOOL, 'notes': {'type': 'array', 'maxItems': 6, 'items': text_schema(1000)}})


def parse_response(text, schema):
    """拒绝重复字段、无效编码、超限正文与不符合当前合同的模型返回。"""
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError
            value[key] = item
        return value
    def invalid_constant(_):
        raise ValueError
    try:
        if not isinstance(text, str) or len(text.encode('utf-8')) > 192 * 1024:
            raise ValueError
        value = json.loads(text, object_pairs_hook=unique, parse_constant=invalid_constant)
        if validate(value, schema):
            raise ValueError
        def nonblank(item):
            if isinstance(item, dict):
                if set(item) == set(LANGUAGES) and any(not value.strip() for value in item.values()):
                    raise ValueError
                for value in item.values():
                    nonblank(value)
            elif isinstance(item, list):
                for value in item:
                    nonblank(value)
        nonblank(value)
        return value
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise AcademyError('authoring-invalid') from exc


def validate_plan(plan):
    """阶段和先修关系必须可顺序学习，不能靠相似标题拼接身份。"""
    stages = [stage['id'] for stage in plan['stages']]
    if len(set(stages)) != len(stages):
        raise AcademyError('authoring-invalid')
    seen, used = set(), []
    for lesson in plan['lessons']:
        stage = lesson['stage_id']
        if lesson['id'] in seen or stage not in stages or any(identity not in seen for identity in lesson['prerequisites']):
            raise AcademyError('authoring-invalid')
        if not used or used[-1] != stage:
            used.append(stage)
        seen.add(lesson['id'])
    if used != stages:
        raise AcademyError('authoring-invalid')


def validate_content(plan, content):
    """内容与规划逐课对应；仅解析 Python，不在制课时执行生成代码。"""
    if [lesson['id'] for lesson in content['lessons']] != [lesson['id'] for lesson in plan['lessons']]:
        raise AcademyError('authoring-invalid')
    for outline, lesson in zip(plan['lessons'], content['lessons']):
        checks = lesson['knowledge_checks']
        if len({check['id'] for check in checks}) != len(checks):
            raise AcademyError('authoring-invalid')
        for check in checks:
            ids = [option['id'] for option in check['options']]
            if len(ids) != len(set(ids)) or check['answer_id'] not in ids:
                raise AcademyError('authoring-invalid')
        if outline['mode'] == 'python':
            try:
                for key in ('starter', 'checker'):
                    if not lesson[key].strip():
                        raise ValueError
                    ast.parse(lesson[key])
            except (ValueError, SyntaxError) as exc:
                raise AcademyError('authoring-invalid') from exc
        elif lesson['starter'] or lesson['checker']:
            raise AcademyError('authoring-invalid')


async def generate_step(phase, payload, adapter, config):
    """每阶段只请求一次模型，保留实际模型身份，不授予工具。"""
    schema = {'planning': PLAN_SCHEMA, 'writing': CONTENT_SCHEMA, 'reviewing': REVIEW_SCHEMA}[phase]
    request = {'task': 'academy-' + phase, **payload, 'response_schema': schema}
    async with asyncio.timeout(120 if phase == 'writing' else 60):
        response = await adapter.complete(
            [SystemMessage(tr('academy.authoring.' + phase)), UserMessage(json.dumps(request, ensure_ascii=False))],
            config=config.merged(max_tokens=16000 if phase == 'writing' else 5000), tools=[],
        )
    if response.tool_uses:
        raise AcademyError('authoring-invalid')
    return parse_response(response.text, schema), response.model or config.model


def course_files(identity, plan, content):
    """宿主分配路径和资源键，模型只能提供有界内容，不能选择写入位置。"""
    resources = {language: {} for language in LANGUAGES}
    files = {}
    def resource(key, values):
        for language in LANGUAGES:
            resources[language][key] = values[language]
        return key
    manifest = {'schema': 1, 'id': identity, 'version': '1.0.0', 'source_revision': PROMPT_VERSION,
        'title_key': resource('course.title', plan['title']),
        'description_key': resource('course.description', plan['description']),
        'assumptions_key': resource('course.assumptions', plan['assumptions']), 'references': [], 'lessons': []}
    for stage in plan['stages']:
        resource('stage.' + stage['id'], stage['title'])
        resource('stage.' + stage['id'] + '.goal', stage['goal'])
    for index, (outline, lesson) in enumerate(zip(plan['lessons'], content['lessons'])):
        prefix = 'lesson.' + outline['id']
        row = {'id': outline['id'], 'mode': outline['mode'], 'order': index + 1,
            'milestone': index + 1 == len(plan['lessons']) or plan['lessons'][index + 1]['stage_id'] != outline['stage_id'],
            'title_key': resource(prefix + '.title', outline['title']), 'goal_key': resource(prefix + '.goal', outline['goal']),
            'reading_key': resource(prefix + '.reading', lesson['reading']), 'exercise_key': resource(prefix + '.exercise', lesson['exercise']),
            'rubric_key': resource(prefix + '.rubric', lesson['rubric']), 'stage_key': 'stage.' + outline['stage_id'],
            'stage_goal_key': 'stage.' + outline['stage_id'] + '.goal', 'duration_minutes': outline['duration_minutes'],
            'prerequisites': outline['prerequisites'], 'references': [], 'knowledge_checks': []}
        if outline['mode'] == 'python':
            base = 'lessons/' + outline['id']
            row.update(starter=base + '/starter', check=base + '/check.py', run_command=['python', 'main.py'],
                       source_manifest=[{'path': 'main.py', 'symbol': ''}])
            files[base + '/starter/main.py'] = lesson['starter']
            files[base + '/check.py'] = lesson['checker']
        for check in lesson['knowledge_checks']:
            key = prefix + '.check.' + check['id']
            row['knowledge_checks'].append({'id': check['id'], 'prompt_key': resource(key + '.prompt', check['prompt']),
                'answer_id': check['answer_id'],
                'hint_keys': [resource(key + '.hint.' + str(index), hint) for index, hint in enumerate(check['hints'])],
                'options': [{'id': option['id'], 'label_key': resource(key + '.' + option['id'] + '.label', option['label']),
                             'feedback_key': resource(key + '.' + option['id'] + '.feedback', option['feedback'])} for option in check['options']]})
        manifest['lessons'].append(row)
    files['course.json'] = json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'
    for language, values in resources.items():
        files['locales/' + language + '.json'] = json.dumps(values, ensure_ascii=False, indent=2) + '\n'
    return files


class AcademyCourseDesigner:
    """持久生成意图与阶段，观察和重启均不重新调用模型。"""

    def __init__(self, store, catalog, change, mark_started):
        self.store, self.catalog = store, catalog
        self.change, self.mark_started = change, mark_started
        self._active = set()
        self._lock = threading.RLock()

    def _record(self, identity):
        value = self.store.get('course-design', identity)
        if value is None:
            raise AcademyError('design-not-found')
        if not re.fullmatch(r'design_[a-f0-9]{32}', identity) or validate(value, SCHEMAS['AcademyDesign']):
            raise AcademyError('store-invalid')
        return value

    def design(self, identity):
        value = self._record(identity)
        with self._lock:
            if value['status'] == 'generating' and identity not in self._active:
                value.update(status='unknown', error_code='authoring-interrupted')
        return value

    def designs(self):
        return [self.design(value['id']) for value in self.store.list('course-design')]

    def _ended(self, identity):
        with self._lock:
            self._active.discard(identity)

    def create(self, request_id, topic, background='', *, model='', schedule=None):
        if not isinstance(topic, str) or not topic.strip() or len(topic) > 500 or not isinstance(background, str) or len(background) > 2000:
            raise AcademyError('invalid-request')
        def create():
            with self._lock:
                if len(self._active) >= 2:
                    raise AcademyError('authoring-busy')
                value = {'id': 'design_' + uuid.uuid4().hex, 'request_id': request_id, 'topic': topic,
                    'background': background, 'course_id': 'personal_' + uuid.uuid4().hex,
                    'language': current_language.get(), 'model': model, 'created_at': time.time(),
                    'status': 'generating', 'phase': 'planning', 'review_notes': []}
                self.mark_started()
                self.store.put('course-design', value)
                if schedule is None:
                    value.update(status='failed', error_code='authoring-unavailable')
                    self.store.put('course-design', value)
                else:
                    self._active.add(value['id'])
                    try:
                        future = schedule(value['id'])
                        future.add_done_callback(lambda _: self._ended(value['id']))
                    except Exception:
                        self._active.discard(value['id'])
                        value.update(status='failed', error_code='authoring-unavailable')
                        self.store.put('course-design', value)
                return {'design': value}
        return self.change(request_id, 'design', 'academy-courses', {'topic': topic, 'background': background}, create)

    def _phase(self, value, phase, model):
        value.update(phase=phase, model=model)
        self.store.put('course-design', value)

    def _publish(self, identity, plan, content):
        target_root = self.catalog.additional_root
        if target_root is None:
            target_root = self.store.root / 'courses'
            self.catalog.additional_root = target_root
        if target_root.is_symlink():
            raise AcademyError('invalid-path')
        target_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.course-design-', dir=self.store.root) as directory:
            staged = Path(directory) / identity
            staged.mkdir()
            for path, text in course_files(identity, plan, content).items():
                file = file_path(staged, path)
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(text, encoding='utf-8')
            AcademyCatalog(Path(directory)).raw(identity)
            files = read_files(staged)
            target = target_root / identity
            # 目标目录和每个文件独占创建；全部就绪后才公开目录发现标记。
            target.mkdir()
            for path, text in files.items():
                if path == 'course.json':
                    continue
                file = file_path(target, path)
                file.parent.mkdir(parents=True, exist_ok=True)
                with file.open('x', encoding='utf-8') as stream:
                    stream.write(text)
            os.link(staged / 'course.json', target / 'course.json')

    def _finish(self, value):
        try:
            self.store.put('course-design', value)
        finally:
            self._ended(value['id'])

    async def generate(self, identity, adapter, config):
        value = await asyncio.to_thread(self._record, identity)
        token = current_language.set(value['language'])
        try:
            payload = {'topic': value['topic'], 'background': value['background']}
            plan, model = await generate_step('planning', payload, adapter, config)
            validate_plan(plan)
            await asyncio.to_thread(self._phase, value, 'writing', model)
            content, model = await generate_step('writing', {**payload, 'plan': plan}, adapter, config)
            validate_content(plan, content)
            await asyncio.to_thread(self._phase, value, 'reviewing', model)
            review, model = await generate_step('reviewing', {'plan': plan, 'content': content}, adapter, config)
            value.update(model=model, review_notes=review['notes'])
            if review['approved'] and not review['notes']:
                await join_cleanup(asyncio.create_task(asyncio.to_thread(self._publish, value['course_id'], plan, content)))
                value['status'] = 'ready'
            else:
                value.update(status='failed', error_code='authoring-review-failed')
        except asyncio.CancelledError:
            value.update(status='unknown', error_code='authoring-interrupted')
            raise
        except TimeoutError:
            value.update(status='unknown', error_code='authoring-timeout')
        except AcademyError as exc:
            value.update(status='failed', error_code=exc.code)
        except Exception:
            value.update(status='unknown', error_code='authoring-failed')
        finally:
            current_language.reset(token)
            await join_cleanup(asyncio.create_task(asyncio.to_thread(self._finish, value)))
