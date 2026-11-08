"""统一生产入口的隔离配置和确定性模型传输夹具。"""
from __future__ import annotations

import json
from pathlib import Path

from harness.app.git_process import run_git
from tests.agent_configuration_fixture import RecordingTransport


def product_config(root):
    repo = root / 'repo'
    repo.mkdir()
    for args in (('init', '--initial-branch=main'), ('config', 'user.name', 'Fixture'),
                 ('config', 'user.email', 'fixture@example.invalid')):
        run_git(repo, *args)
    (repo / 'main.txt').write_text('fixture\n')
    run_git(repo, 'add', '.')
    run_git(repo, 'commit', '-m', 'fixture')
    def profile(tools):
        return {'repo': str(repo), 'description': 'Fixture profile', 'source_instance_ids': ['local'],
                'actions': ['read'], 'tools': tools, 'max_turns': 8, 'max_seconds': 120}
    value = {'version': 2, 'instance_id': 'product', 'state_dir': str(root / 'state'),
        'language': 'en', 'model': 'fixture', 'base_url': 'https://fixture.invalid/v1',
        'permission_policy': 'full_access', 'interval': .01, 'once_timeout': 60,
        'sources': {'local': {'kind': 'local', 'config': {'namespace': 'product'},
                             'actors': ['human:1'], 'threads': ['work']}},
        'profiles': {'work': profile(['read']), 'integrator': profile(['read', 'write']), 'reviewer': profile(['read'])},
        'default_profiles': {'local': 'work'}, 'system_profiles': {'integrate': 'integrator', 'review': 'reviewer'},
        'policy': {'mode': 'auto', 'rules': [{'id': 'work', 'name': 'Work', 'enabled': True, 'instruction': 'Accept a settled explicit implementation request.'}],
            'sources': {'local': {'allowed': [{'id': 'human:1'}], 'scope': {
                'source_instance_ids': ['local'], 'thread_ids': ['$current'], 'actions': ['read', 'reply'],
                'tools': ['read', 'write'], 'budget': {'max_turns': 30, 'max_seconds': 600}}}}}}
    path = root / 'superagent.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    return path, value


class ProductTransport(RecordingTransport):
    def __init__(self, *, report=None):
        super().__init__()
        self.report = report or {'status': 'completed', 'summary': 'Verified.', 'reply': 'Verified result.'}
        self.frames = []
        self.gate = self.entered = None

    async def post_stream(self, url, *, headers, body, timeout):
        request = json.loads(body)
        self.requests.append(request)
        text = next(m['content'] for m in reversed(request['messages']) if m.get('role') == 'user')
        if isinstance(text, list):
            text = ''.join(p.get('text', '') for p in text)
        if self.gate is not None:
            self.entered.set()
            await self.gate.wait()
        try:
            frame = json.loads(text.rsplit('\n', 1)[-1])
        except ValueError:
            frame = None
        self.frames.append(frame)
        if frame and 'evaluation_id' in frame:
            evidence = frame['triggers'][0]['body']['text']
            answer = {'kind': 'accept', 'reason': 'Explicit settled request.', 'title': 'Fixture work',
                'intent_evidence': evidence,
                'direction': {'implementation_requested': True, 'target_basis': 'specified', 'requires_design_decision': False, 'evidence': evidence},
                'rule_assessments': [{'rule_id': r['id'], 'matched': True, 'reason': 'In scope.'} for r in frame['policy']['rules'] if r['enabled']]}
        elif frame and 'default_profile' in frame:
            answer = {'plan': {'nodes': [{'node_id': 'work', 'role': 'work', 'profile_id': frame['default_profile'], 'instruction':'Answer the authorized request.', 'dependencies': []}]},
                      'acceptance': {'kind': 'single_worker_report', 'schema_version': 1}}
        else:
            answer = self.report
        yield ('data: ' + json.dumps({'choices': [{'delta': {'content': json.dumps(answer)}, 'finish_reason': 'stop'}]}) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


def review_contract(request):
    """按稳定 JSON 字段读取复核契约，不依赖当前提示语言或自然语言前缀。"""
    decoder = json.JSONDecoder()
    for message in request.get('messages', []):
        text = message.get('content')
        if not isinstance(text, str):
            continue
        for position, character in enumerate(text):
            if character != '{':
                continue
            try:
                value, _ = decoder.raw_decode(text[position:])
            except ValueError:
                continue
            if isinstance(value, dict) and {'candidate_artifact_id', 'acceptance_hash', 'available_evidence', 'criteria'} <= set(value):
                return value
    return None
