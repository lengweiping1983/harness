"""显式在线验收匿名免费模型，用隔离项目核对真实开发与工具执行。"""
import asyncio
from dataclasses import fields
import json
import os
from pathlib import Path
import tempfile
import uuid

from harness import Harness, HarnessConfig
from harness.llm.kilo_free import BASE_URL, free_models
from harness.llm.providers import ProviderConnections, decode
import urllib.request


async def main():
    """不使用账号、用户项目或已有对话，只在临时目录执行写读验收。"""
    if os.environ.get('HARNESS_LIVE_KILO') != '1':
        raise SystemExit('HARNESS_LIVE_KILO=1 required')
    with urllib.request.urlopen(BASE_URL + '/models', timeout=15) as response:
        catalog = free_models(decode(response.read(8 * 1024 * 1024 + 1))['data'])
    model = os.environ.get('HARNESS_LIVE_KILO_MODEL', 'kilo-auto/free')
    if model not in {row['id'] for row in catalog}:
        raise SystemExit('Selected model is not in the current free pool')
    with tempfile.TemporaryDirectory(prefix='harness-kilo-live-') as directory:
        root = Path(directory)
        store = ProviderConnections(root/'state')
        store.save([{'id': 'free', 'type': 'kilo-free', 'name': 'Kilo Free', 'connections': [{
            'id': 'endpoint', 'name': 'Kilo Free', 'base_url': BASE_URL, 'enabled': True,
            'auth': {'type': 'anonymous', 'env': {}}, 'keys': [], 'model_catalog': catalog,
            'models': [{'id': 'model', 'model': model, 'name': model, 'enabled': True}]}]}], store.revision)
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags.update(enable_fs=True, enable_interaction=True)
        app = Harness(HarnessConfig(home=root/'state', cwd=root, language='en', permission_policy='full_access',
            max_steps=8, max_retries=0, network_timeout=60, **flags), codex_gateway_home=root/'gateway')
        try:
            marker = 'verified_' + uuid.uuid4().hex
            session = app.create_session()
            result = await app.run_async(session,
                f'Use the actual file tools to create solution.py with a function answer() returning "{marker}". '
                'Then read solution.py with the read tool to verify the saved code. Do not use a shell. '
                'After both tools succeed reply exactly VERIFIED.')
            assert result.reason == 'completed', result.error_info
            source = (root/'solution.py').read_text(encoding='utf-8')
            namespace = {}
            exec(compile(source, 'solution.py', 'exec'), namespace)
            assert namespace['answer']() == marker
            calls = [event for event in session.events_of_type('tool/call') if event.data.get('status') == 'running']
            results = session.events_of_type('tool/result')
            assert {'write', 'read'} <= {event.data['name'] for event in calls}
            assert {event.data['call_id'] for event in calls} == {event.data['call_id'] for event in results}
            assert all(not row['is_error'] for event in results for row in event.data['results'])
            followup = await app.run_async(session, 'Return only the exact string returned by answer() from your previous work. Do not call tools.')
            assert followup.reason == 'completed' and followup.text.strip() == marker, followup.to_dict()
            other = app.create_session()
            isolated = await app.run_async(other, 'If this conversation contains no source code, reply exactly ISOLATED. Do not call tools.')
            assert isolated.reason == 'completed' and isolated.text.strip() == 'ISOLATED', isolated.to_dict()
            assert app.config.api_key == ''
            report = {'passed': True, 'model': model, 'free_catalog_count': len(catalog), 'api_key_configured': False,
                      'generated_python_executed': True, 'tool_names': [event.data['name'] for event in calls],
                      'same_conversation_history': True, 'other_conversation_isolated': True}
            destination = os.environ.get('HARNESS_LIVE_KILO_REPORT')
            if destination:
                Path(destination).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(report, ensure_ascii=False))
        finally:
            await app.close()


if __name__ == '__main__':
    asyncio.run(main())
