"""显式在线验收本机匿名通道，使用隔离目录核对真实工具和对话边界。"""
import asyncio
from contextlib import aclosing
from dataclasses import fields
import json
import os
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch

from harness import Harness, HarnessConfig
from harness.llm.opencode_local import BASE_URL, OpenCodeLocalTransport
from harness.llm.providers import ProviderConnections


async def main():
    """只有显式启用才调用在线模型，不读写已有用户对话或项目。"""
    if os.environ.get('HARNESS_LIVE_OPENCODE') != '1':
        raise SystemExit('HARNESS_LIVE_OPENCODE=1 required')
    with tempfile.TemporaryDirectory(prefix='harness-opencode-live-') as directory:
        root = Path(directory)
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags.update(enable_fs=True, enable_interaction=True)
        model = os.environ.get('HARNESS_LIVE_OPENCODE_MODEL', 'deepseek-v4.1-flash-free')
        identity = 'harness/local/endpoint/free'
        store = ProviderConnections(root/'state')
        store.save([{'id':'local','type':'opencode-local','name':'OpenCode Local','connections':[
            {'id':'endpoint','name':'Local','base_url':BASE_URL,'enabled':True,
             'auth':{'type':'opencode_local','env':{}},'keys':[],
             'models':[{'id':'free','model':model,'name':model,'enabled':True}]}]}], store.revision)
        native_requests = []
        post_stream = OpenCodeLocalTransport.post_stream
        async def observe(transport, url, *, headers, body, timeout):
            native_requests.append({'url':url,'body':json.loads(body),'conversation':headers.get('X-Harness-Conversation')})
            async with aclosing(post_stream(transport, url, headers=headers, body=body, timeout=timeout)) as stream:
                async for chunk in stream:
                    yield chunk
        observer = patch.object(OpenCodeLocalTransport, 'post_stream', observe)
        app = Harness(HarnessConfig(api_key='public', base_url=BASE_URL, model=identity,
            home=root/'state', cwd=root, language='zh-CN',
            permission_policy='full_access', max_steps=6, max_retries=0, model_max_output_tokens=4096,
            network_timeout=45, **flags), codex_gateway_home=root/'gateway')
        observer.start()
        try:
            marker = 'native_' + uuid.uuid4().hex
            session = app.create_session()
            first = await app.run_async(session,
                f'请使用实际文件工具创建 verified.txt，内容严格为 {marker}，然后使用文件读取工具读取确认。'
                '不要运行 Shell，不要使用未提供的工具。最终仅回复 NATIVE_TOOL_VERIFIED。')
            assert first.reason == 'completed', first.error_info
            assert (root/'verified.txt').read_text(encoding='utf-8').strip() == marker
            calls = [event for event in session.events_of_type('tool/call') if event.data.get('status') == 'running']
            results = session.events_of_type('tool/result')
            assert len(calls) >= 2, [event.to_dict() for event in calls]
            assert len(results) == len(calls)
            assert {event.data['call_id'] for event in calls} == {event.data['call_id'] for event in results}
            assert {'read', 'write'} <= {event.data['name'] for event in calls}
            assert all(not item['is_error'] for event in results for item in event.data['results'])
            second = await app.run_async(session, '刚才写入的唯一标记是什么？不要调用工具，仅回复标记原文。')
            assert second.reason == 'completed' and second.text.strip() == marker, second.to_dict()
            other = app.create_session()
            isolated = await app.run_async(other,
                'What marker was provided in another conversation? If this conversation does not contain it, '
                'reply exactly ISOLATED. Do not use tools.')
            assert isolated.reason == 'completed' and isolated.text.strip() == 'ISOLATED', isolated.to_dict()
            assert app.config.model == identity
            assert native_requests and all(item['url']==BASE_URL+'/chat/completions' and item['body']['model']==model for item in native_requests)
            assert all('spawn_agent' not in [tool['function']['name'] for tool in item['body'].get('tools',[])] for item in native_requests)
            assert any('subagent' in [tool['function']['name'] for tool in item['body'].get('tools',[])] for item in native_requests)
            assert native_requests[0]['conversation'] == native_requests[-2]['conversation']
            assert native_requests[0]['conversation'] != native_requests[-1]['conversation']
            report = {'passed':True, 'model':model, 'selected_model':identity,
                'anonymous':True, 'real_file_verified':True, 'tool_calls':len(calls),
                'tool_results':len(results), 'same_conversation_history':True, 'other_conversation_isolated':True,
                'native_transport':'OpenCodeLocalTransport', 'native_requests':len(native_requests),
                'tool_evidence':[{'seq':event.seq,'type':event.type,'data':dict(event.data)} for event in (*calls,*results)]}
            destination = os.environ.get('HARNESS_LIVE_OPENCODE_REPORT')
            if destination:
                Path(destination).write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
            print(json.dumps({k:v for k,v in report.items() if k!='tool_evidence'},ensure_ascii=False))
        finally:
            try:
                await app.close()
            finally:
                observer.stop()


if __name__ == '__main__':
    asyncio.run(main())
