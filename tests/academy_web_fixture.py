"""Academy 浏览器回归专用模型，点评引用真实提交快照。"""
from __future__ import annotations

import asyncio
import json

from harness.llm.transport import Transport

from .web_fixture import main
from .test_academy_authoring import content as design_content, plan as design_plan
from .test_academy_tutor import TutorTransport


class AcademyAssessmentTransport(Transport):
    @staticmethod
    def response(payload):
        """仅在隔离夹具中用确定内容走实际制课与点评调用链。"""
        task = payload.get('task')
        if task == 'academy-model':
            return 'Observed model response / 模型回答：Monday 09:00 is supported; weekend hours are unknown.'
        if task == 'academy-planning':
            value = design_plan()
            if payload['topic'].startswith('Python'):
                value['lessons'][0]['mode'] = 'python'
            return value
        if task == 'academy-writing':
            value = design_content()
            if payload['plan']['lessons'][0]['mode'] == 'python':
                value['lessons'][0].update(starter='def double(value):\n    return value * 2\n\nprint(double(3))\n',
                    checker='from main import double\nassert double(3) == 6\nassert double(0) == 0\nassert double(-2) == -4\nprint("CHECK PASS")\n')
            return value
        if task == 'academy-reviewing':
            return {'approved': True, 'notes': []}
        sources = payload['sources']
        file_source = next((key for key in sources if key.startswith('file:')), 'note')
        file_quote = sources[file_source].splitlines()[0][:160]
        dimensions = []
        for identity in ('concept', 'goal', 'reasoning', 'verification', 'reflection'):
            source = 'note' if sources.get('note') and identity != 'verification' else file_source
            quote = sources[source][:160] if source == 'note' else file_quote
            dimensions.append({'id': identity, 'score': 3,
                               'reason': 'This quotation belongs to the frozen submission.',
                               'evidence': [{'source': source, 'quote': quote}],
                               'next_step': 'Compare the explanation with one new example.'})
        return {'summary': 'Fixture assessment completed without running student code.',
                'dimensions': dimensions, 'next_steps': ['Explain one observable result in your own words.']}

    async def post_stream(self, url, *, headers, body, timeout):
        """返回固定量规，来源和引用由本次请求的冻结快照取得。"""
        request = json.loads(body)
        message = next(item for item in reversed(request['messages']) if item.get('role') == 'user')
        content = message['content']
        if isinstance(content, list):
            content = ''.join(item.get('text', '') for item in content if item.get('type') == 'text')
        try:
            payload = json.loads(content)
        except ValueError:
            payload = {'task': 'academy-model', 'prompt': content}
        native_tutor = payload.get('task') == 'academy-tutor'
        if not native_tutor and 'ACADEMY_MODEL_STOP_STREAM' in content:
            yield ('data: ' + json.dumps({'model': 'fixture-model-stop', 'choices': [{'delta': {'content': 'MODEL_STOP_STREAM_VISIBLE'}, 'finish_reason': None}]}) + '\n\n').encode('utf-8')
            await asyncio.Event().wait()
            return
        if native_tutor and 'ACADEMY_STOP_STREAM' in payload.get('question', ''):
            yield ('data: ' + json.dumps({'model': 'fixture-stop', 'choices': [{'delta': {'content': 'STOP_STREAM_VISIBLE'}, 'finish_reason': None}]}) + '\n\n').encode('utf-8')
            await asyncio.Event().wait()
            return
        assessment = None if native_tutor else self.response(payload)
        delta = TutorTransport.response(request) if native_tutor else {'role': 'assistant', 'content': assessment if isinstance(assessment, str) else json.dumps(assessment)}
        chunks = [
            {'model': 'fixture-assessor', 'choices': [{'delta': delta, 'finish_reason': None}]},
            {'choices': [{'delta': {}, 'finish_reason': 'tool_calls' if 'tool_calls' in delta else 'stop'}], 'usage': {'prompt_tokens': 30, 'completion_tokens': 10}},
        ]
        for chunk in chunks:
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode('utf-8')
        yield b'data: [DONE]\n\n'


if __name__ == '__main__':
    main(transport=AcademyAssessmentTransport())
