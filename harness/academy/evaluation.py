"""面向本课目标的形成性点评；只读提交快照，不执行学生代码。"""
from __future__ import annotations

import asyncio
import json

from harness.i18n import tr
from harness.llm.types import SystemMessage, UserMessage
from harness.tools.schema import validate

from .errors import AcademyError
from .protocol import SCHEMAS


DIMENSIONS = ('concept', 'goal', 'reasoning', 'verification', 'reflection')
PROMPT_VERSION = 'academy-formative-v1'
MAX_SUBMISSION_BYTES = 48 * 1024


def _resolve_schema(value):
    """给模型和轻量校验器展开本模块协议引用，保持一份字段来源。"""
    if isinstance(value, dict):
        if '$ref' in value:
            return _resolve_schema(SCHEMAS[value['$ref'].rsplit('/', 1)[-1]])
        return {key: _resolve_schema(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_schema(item) for item in value]
    return value


ASSESSMENT_SCHEMA = _resolve_schema(SCHEMAS['AcademyAssessment'])


def parse_assessment(text, sources):
    """只接收五项完整量规，并核对每段引用确实来自这次提交。"""
    try:
        if not isinstance(text, str) or len(text) > 24000:
            raise ValueError
        value = json.loads(text)
        if validate(value, ASSESSMENT_SCHEMA):
            raise ValueError
        dimensions = value['dimensions']
        if len(dimensions) != 5 or {item['id'] for item in dimensions} != set(DIMENSIONS):
            raise ValueError
        if not 1 <= len(value['next_steps']) <= 3 or not value['summary'].strip() or len(value['summary']) > 2000:
            raise ValueError
        if any(not step.strip() or len(step) > 500 for step in value['next_steps']):
            raise ValueError
        for item in dimensions:
            if not item['reason'].strip() or len(item['reason']) > 1000 or not item['next_step'].strip() or len(item['next_step']) > 500:
                raise ValueError
            if len(item['evidence']) > 3 or (item['score'] is not None and not item['evidence']):
                raise ValueError
            # bool 在 Python 中是 int 的子类，不能当作分数接收。
            if item['score'] is not None and (type(item['score']) is not int or not 0 <= item['score'] <= 5):
                raise ValueError
            for evidence in item['evidence']:
                quote = evidence['quote']
                if not quote.strip() or len(quote) > 500 or quote not in sources.get(evidence['source'], ''):
                    raise ValueError
        value['dimensions'] = sorted(dimensions, key=lambda item: DIMENSIONS.index(item['id']))
        return value
    except (ValueError, KeyError, TypeError) as exc:
        raise AcademyError('assessment-invalid') from exc


async def assess_submission(snapshot, adapter, config):
    """使用宿主已配置的模型，无工具、无课程执行、无会话写入。"""
    sources = snapshot['sources']
    payload = {'lesson': snapshot['lesson'], 'sources': sources,
               'response_schema': ASSESSMENT_SCHEMA, 'dimension_ids': list(DIMENSIONS)}
    # 提示词和调用语言随提交冻结；页面切换语言不改写已有反馈。
    async with asyncio.timeout(60):
        response = await adapter.complete(
            [SystemMessage(tr('academy.assessment.system')), UserMessage(json.dumps(payload, ensure_ascii=False))],
            config=config.merged(max_tokens=6000), tools=[],
        )
    if response.tool_uses:
        raise AcademyError('assessment-invalid')
    return parse_assessment(response.text, sources), response.model or config.model
