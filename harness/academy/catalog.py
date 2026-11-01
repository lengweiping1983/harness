"""数据驱动的课程包目录；教学内容不依赖框架语言资源或固定课号。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from harness.core.language import current_language
from harness.tools.schema import validate

from .protocol import obj, array, TEXT, BOOL, VERSION, MODE, SCHEMAS

from .errors import AcademyError
from .files import file_path, fingerprint, read_files

IDENTITY = r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}'


class AcademyCatalog:
    """课程包声明资源键、源码目录、运行入口和独立检查文件。"""

    def __init__(self, root: Path | None = None):
        self.root = (root or Path(__file__).parent / 'content').resolve()
        self.additional_root = None

    def directory(self, course_id):
        if not isinstance(course_id, str) or not re.fullmatch(IDENTITY, course_id):
            raise AcademyError('course-not-found')
        candidates = [root / course_id for root in [self.root, self.additional_root] if root is not None and (root / course_id).exists()]
        if len(candidates) > 1:
            raise AcademyError('catalog-invalid')
        if not candidates or candidates[0].is_symlink() or not candidates[0].is_dir():
            raise AcademyError('course-not-found')
        return candidates[0]

    def raw(self, course_id: str):
        directory = self.directory(course_id)
        try:
            course = json.loads((directory / 'course.json').read_text(encoding='utf-8'))
            reference = SCHEMAS['AcademySourceReference']
            knowledge_option = obj({'id': TEXT, 'label_key': TEXT, 'feedback_key': TEXT})
            knowledge_check = obj({'id': TEXT, 'prompt_key': TEXT,
                'options': {'type': 'array', 'items': knowledge_option, 'minItems': 2, 'maxItems': 6},
                'answer_id': TEXT, 'hint_keys': {'type': 'array', 'items': TEXT, 'maxItems': 3}})
            manifest_lesson = obj({'id': TEXT, 'order': VERSION, 'mode': MODE, 'milestone': BOOL, 'title_key': TEXT,
                'goal_key': TEXT, 'reading_key': TEXT, 'starter': TEXT, 'check': TEXT,
                'run_command': array(TEXT), 'source_manifest': array(reference),
                'references': array(reference), 'prerequisites': array(TEXT),
                'stage_key': TEXT, 'stage_goal_key': TEXT, 'exercise_key': TEXT, 'rubric_key': TEXT,
                'model_prompt_key': TEXT, 'model_system_key': TEXT,
                'duration_minutes': {'type': 'integer', 'minimum': 1, 'maximum': 180},
                'knowledge_checks': {'type': 'array', 'items': knowledge_check, 'minItems': 1, 'maxItems': 8},
                'trace_labels': {'type': 'object', 'additionalProperties': TEXT}},
                ['id', 'order', 'milestone', 'title_key', 'goal_key', 'reading_key', 'references', 'prerequisites'])
            manifest = obj({'schema': {'type': 'integer', 'const': 1}, 'id': TEXT, 'version': TEXT,
                'source_revision': TEXT, 'title_key': TEXT, 'description_key': TEXT,
                'references': array(TEXT), 'lessons': array(manifest_lesson), 'assumptions_key': TEXT},
                ['schema', 'id', 'version', 'source_revision', 'title_key', 'description_key', 'references', 'lessons'])
            if validate(course, manifest):
                raise ValueError
            if (course['schema'] != 1 or type(course['schema']) is not int or course['id'] != course_id or
                    not isinstance(course['version'], str) or not course['version'] or
                    not isinstance(course['lessons'], list) or not course['lessons']):
                raise ValueError
            ids = set()
            for order, lesson in enumerate(course['lessons'], 1):
                if (not re.fullmatch(IDENTITY, lesson['id']) or lesson['id'] in ids or
                        lesson['order'] != order or type(lesson['order']) is not int):
                    raise ValueError
                ids.add(lesson['id'])
                for key in ('title_key', 'goal_key', 'reading_key'):
                    if not isinstance(lesson[key], str):
                        raise ValueError
                if lesson.get('mode', 'python') == 'python':
                    command = lesson['run_command']
                    if (not isinstance(command, list) or len(command) < 2 or command[0] != 'python' or
                            not all(isinstance(value, str) and value and '\0' not in value for value in command)):
                        raise ValueError
                    starter = file_path(directory, lesson['starter'])
                    checker = file_path(directory, lesson['check'])
                    if not starter.is_dir() or not checker.is_file() or not file_path(starter, command[1]).is_file() or 'source_manifest' not in lesson:
                        raise ValueError
                elif lesson.get('mode') == 'model':
                    if (any(key in lesson for key in ('starter', 'check', 'run_command', 'source_manifest')) or
                            not lesson.get('knowledge_checks') or any(key not in lesson for key in ('model_prompt_key', 'model_system_key'))):
                        raise ValueError
                elif any(key in lesson for key in ('starter', 'check', 'run_command', 'source_manifest')) or not lesson.get('knowledge_checks'):
                    raise ValueError
                if any(prerequisite not in ids - {lesson['id']} for prerequisite in lesson['prerequisites']):
                    raise ValueError
                checks = lesson.get('knowledge_checks', [])
                if len({check['id'] for check in checks}) != len(checks):
                    raise ValueError
                for check in checks:
                    option_ids = [option['id'] for option in check['options']]
                    if (not re.fullmatch(IDENTITY, check['id']) or
                            any(not re.fullmatch(IDENTITY, identity) for identity in option_ids) or
                            len(set(option_ids)) != len(option_ids) or check['answer_id'] not in option_ids):
                        raise ValueError
            # 每个包拥有自己的双语资源；框架只按显式键读取，新增课程无需重建 UI。
            resources = [self.resources(directory, language) for language in ('en', 'zh-CN')]
            if resources[0].keys() != resources[1].keys():
                raise ValueError
            keys = [course['title_key'], course['description_key']]
            if 'assumptions_key' in course:
                keys.append(course['assumptions_key'])
            keys.extend(lesson[key] for lesson in course['lessons'] for key in ('title_key', 'goal_key', 'reading_key'))
            keys.extend(key for lesson in course['lessons'] for key in lesson.get('trace_labels', {}).values())
            keys.extend(lesson[key] for lesson in course['lessons'] for key in ('stage_key', 'stage_goal_key', 'exercise_key', 'rubric_key', 'model_prompt_key', 'model_system_key') if key in lesson)
            knowledge_keys = [key for lesson in course['lessons'] for check in lesson.get('knowledge_checks', [])
                              for key in [check['prompt_key'], *check['hint_keys'],
                                          *(option[field] for option in check['options'] for field in ('label_key', 'feedback_key'))]]
            keys.extend(knowledge_keys)
            if any(key not in resources[0] for key in keys):
                raise ValueError
            if any(not resource[key].strip() for resource in resources for key in knowledge_keys):
                raise ValueError
            for lesson in course['lessons']:
                for field, limit in (('model_prompt_key', 8000), ('model_system_key', 8000)):
                    if field in lesson and (lesson.get('mode') != 'model' or any(
                            not resource[lesson[field]].strip() or len(resource[lesson[field]]) > limit for resource in resources)):
                        raise ValueError
                    if field in lesson:
                        for resource in resources:
                            resource[lesson[field]].encode('utf-8')
            return course
        except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise AcademyError('catalog-invalid') from exc

    @staticmethod
    def resources(directory, language):
        values = json.loads((directory / 'locales' / (language + '.json')).read_text(encoding='utf-8'))
        if not isinstance(values, dict) or any(not isinstance(value, str) for value in values.values()):
            raise ValueError
        return values

    def course(self, course_id: str):
        raw = self.raw(course_id)
        resources = self.resources(self.directory(course_id), current_language.get())
        course = {key: raw[key] for key in ('id', 'version', 'source_revision', 'references')}
        course['origin'] = 'provided' if self.directory(course_id).parent == self.root else 'personal'
        course.update(title=resources[raw['title_key']], description=resources[raw['description_key']])
        if 'assumptions_key' in raw:
            course['assumptions'] = resources[raw['assumptions_key']]
        course['lessons'] = [{**{key: lesson[key] for key in ('id', 'order', 'milestone', 'references', 'prerequisites')},
                              'mode': lesson.get('mode', 'python'), 'run_command': lesson.get('run_command', []),
                              'source_manifest': lesson.get('source_manifest', []),
                              'title': resources[lesson['title_key']], 'goal': resources[lesson['goal_key']], 'reading': resources[lesson['reading_key']],
                              'trace_labels': {event: resources[key] for event, key in lesson.get('trace_labels', {}).items()}}
                             for lesson in raw['lessons']]
        for lesson, source in zip(course['lessons'], raw['lessons']):
            for key in ('stage', 'stage_goal', 'exercise', 'rubric'):
                if key + '_key' in source:
                    lesson[key] = resources[source[key + '_key']]
            for key in ('model_prompt', 'model_system'):
                if key + '_key' in source:
                    lesson[key] = resources[source[key + '_key']]
            if 'duration_minutes' in source:
                lesson['duration_minutes'] = source['duration_minutes']
            if 'knowledge_checks' in source:
                lesson['knowledge_checks'] = [{
                    'id': check['id'], 'prompt': resources[check['prompt_key']], 'answer_id': check['answer_id'],
                    'hints': [resources[key] for key in check['hint_keys']],
                    'options': [{'id': option['id'], 'label': resources[option['label_key']],
                                 'feedback': resources[option['feedback_key']]} for option in check['options']],
                } for check in source['knowledge_checks']]
        return course

    def courses(self):
        ids = {path.name for root in [self.root, self.additional_root] if root is not None and root.is_dir()
               for path in root.iterdir() if path.is_dir() and (path / 'course.json').is_file() and not path.name.startswith('.')}
        return [self.course(identity) for identity in sorted(ids)]

    def pin(self, course_id):
        return fingerprint(read_files(self.directory(course_id)))

    def lesson(self, course_id: str, lesson_id: str):
        lesson = next((item for item in self.raw(course_id)['lessons'] if item['id'] == lesson_id), None)
        if lesson is None:
            raise AcademyError('lesson-not-found')
        return lesson

    def starter(self, course_id: str, lesson_id: str):
        lesson = self.lesson(course_id, lesson_id)
        if lesson.get('mode', 'python') in ('study', 'model'):
            return {}
        return read_files(file_path(self.directory(course_id), lesson['starter']))

    def checker(self, course_id: str, lesson_id: str):
        lesson = self.lesson(course_id, lesson_id)
        return file_path(self.directory(course_id), lesson['check'])

    def reference(self, course_id: str, lesson_id: str, path: str):
        lesson = self.lesson(course_id, lesson_id)
        if path not in [item['path'] for item in lesson['references']]:
            raise AcademyError('invalid-path')
        root = Path(__file__).resolve().parents[1] if path.startswith('harness/') else self.directory(course_id)
        relative = path[len('harness/'):] if path.startswith('harness/') else path
        source = file_path(root, relative)
        if not source.is_file() or source.stat().st_size > 256 * 1024:
            raise AcademyError('source-not-found')
        return source.read_text(encoding='utf-8')
