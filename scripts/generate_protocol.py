"""从公共协议生成 TypeScript 与 OpenAPI，支持只读同步检查。"""
import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from harness import Harness, HarnessConfig
from harness.web.context import WebContext
from harness.web.protocol import openapi, typescript
from harness.web.response_contracts import response_contracts
from harness.codex_gateway.protocol import openapi as gateway_openapi
from harness.app.superagent_remote_protocol import worker_openapi

parser = argparse.ArgumentParser()
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='harness-protocol-') as directory:
    instance = Harness(HarnessConfig(home=directory, cwd=directory, api_key='offline', enable_mcp=False, enable_automations=False))
    loop = asyncio.new_event_loop()
    try:
        routes = WebContext(instance, loop).router().templates()
        schema = openapi(routes)
        loop.run_until_complete(instance.close())
    finally:
        loop.close()
outputs = {
    root / 'docs' / 'openapi-worker-v1.json': json.dumps(worker_openapi(), ensure_ascii=False, indent=2) + '\n',
    root / 'docs' / 'openapi-codex-gateway.json': json.dumps(gateway_openapi(), ensure_ascii=False, indent=2) + '\n',
    root / 'docs' / 'openapi-v1.json': json.dumps(schema, ensure_ascii=False, indent=2) + '\n',
    root / 'web' / 'src' / 'lib' / 'protocol.generated.ts': typescript(),
    root / 'web' / 'src' / 'lib' / 'responses.generated.ts': response_contracts(schema),
}
if args.check:
    stale = [str(path.relative_to(root)) for path, content in outputs.items()
             if not path.exists() or path.read_text(encoding='utf-8') != content]
    if stale:
        raise SystemExit('公开协议生成文件未同步：' + ', '.join(stale))
    print('公开协议：同步')
else:
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
