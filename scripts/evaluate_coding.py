"""在独立临时项目中回放可信开发夹具；不提供在线代码的宿主执行。"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import fields
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness import Harness, HarnessConfig
from harness.llm.transport import Transport
from harness.tools.owned_process import spawn_owned

FIXTURE = ROOT / 'tests/fixtures/coding_tasks.json'


class ScriptedTransport(Transport):
    """返回固定工具调用；执行、文件保护及事件持久化仍使用真实运行库。"""

    def __init__(self, calls, root):
        self.calls, self.root, self.index = calls, root, 0

    async def post_stream(self, url, *, headers, body, timeout):
        if self.index < len(self.calls):
            call = self.calls[self.index]
            self.index += 1
            if 'external_change' in call:
                (self.root / 'solution.py').write_text(call['external_change'], encoding='utf-8', newline='')
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': f'fixture-{self.index}',
                'type': 'function', 'function': {'name': call['name'], 'arguments': json.dumps(call['arguments'])}}]}
            finish = 'tool_calls'
        else:
            delta, finish = {'role': 'assistant', 'content': 'Fixture execution finished; inspect independent checks.'}, 'stop'
        for chunk in ({'choices': [{'delta': delta, 'finish_reason': None}]},
                      {'choices': [{'delta': {}, 'finish_reason': finish}]}):
            yield ('data: ' + json.dumps(chunk) + '\n\n').encode()
        yield b'data: [DONE]\n\n'


def load_tasks(path=FIXTURE):
    """拒绝损坏清单、重复身份和目录越界，不补造任务。"""
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('schema') != 1 or type(value.get('schema')) is not int:
        raise ValueError('Invalid coding task schema')
    tasks = value.get('tasks')
    seen = set()
    if not isinstance(tasks, list) or not tasks:
        raise ValueError('Empty coding task set')
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get('id'), str) or not task['id'] or task['id'] in seen:
            raise ValueError('Invalid or duplicate task identity')
        seen.add(task['id'])
        if task.get('category') not in ('bugfix', 'feature', 'refactor', 'recovery') or not isinstance(task.get('prompt'), dict):
            raise ValueError('Invalid task contract')
        if not all(isinstance(task['prompt'].get(lang), str) and task['prompt'][lang] for lang in ('en', 'zh-CN')):
            raise ValueError('Missing task language')
        if not isinstance(task.get('files'), dict) or not task['files'] or not isinstance(task.get('checks'), list) or not task['checks']:
            raise ValueError('Missing task input or acceptance')
        for name, text in task['files'].items():
            if not isinstance(name, str) or Path(name).is_absolute() or ':' in name or '\\' in name or '..' in Path(name).parts or not isinstance(text, str):
                raise ValueError('Unsafe task file')
        if not isinstance(task.get('calls'), list) or not task['calls']:
            raise ValueError('Missing scripted transport')
        for call in task['calls']:
            if not isinstance(call, dict) or call.get('name') not in ('read', 'edit', 'write') or not isinstance(call.get('arguments'), dict):
                raise ValueError('Invalid scripted tool')
            name = call['arguments'].get('path')
            if not isinstance(name, str) or Path(name).is_absolute() or ':' in name or '\\' in name or '..' in Path(name).parts:
                raise ValueError('Unsafe scripted path')
        for check in task['checks']:
            if not isinstance(check, dict) or not isinstance(check.get('expression'), str) or 'expected' not in check:
                raise ValueError('Invalid acceptance check')
    return tasks


CHECK_PROGRAM = '''import json,runpy,sys
sys.path.insert(0, '.')
scope = runpy.run_path('solution.py')
checks = json.loads(sys.argv[1])
for check in checks:
    actual = eval(check['expression'], scope)
    assert actual == check['expected'], (check['expression'], actual, check['expected'])
'''


async def independent_check(root, checks):
    """仅执行仓库可信夹具；所属进程回收不等于操作系统沙箱。"""
    env = {key: value for key, value in os.environ.items() if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
    process = await spawn_owned([sys.executable, '-X', 'utf8', '-I', '-c', CHECK_PROGRAM, json.dumps(checks)], cwd=str(root), env=env)
    async def collect():
        output, error, code = await asyncio.gather(process.stdout.read(65536), process.stderr.read(65536), process.wait())
        return code == 0, error.decode('utf-8')[-4096:]
    try:
        return await asyncio.wait_for(collect(), timeout=10)
    finally:
        await process.stop_owned()
        process.close()


async def evaluate_task(task, *, mode='offline-scripted', repetition=1, token_budget=100000):
    """每项任务拥有独立目录、运行标识、模型传输和验收结果。"""
    if mode != 'offline-scripted':
        raise ValueError('Online evaluation is unavailable until an isolated execution provider is verified')
    started = time.perf_counter()
    result = {'task_id': task['id'], 'category': task['category'], 'repetition': repetition,
              'evaluation_id': uuid.uuid4().hex, 'passed': False, 'tool_calls': 0, 'errors': []}
    with tempfile.TemporaryDirectory(prefix='harness-coding-eval-') as directory:
        root = Path(directory)
        for name, text in task['files'].items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8', newline='')
        flags = {field.name: False for field in fields(HarnessConfig) if field.name.startswith('enable_')}
        flags.update(enable_fs=True, enable_fs_observation_policy=True)
        kwargs = dict(home=root / '.state', cwd=root, api_key='fixture-only', model='fixture-model', language='en',
                      permission_policy='full_access', max_steps=16, max_retries=0, token_budget=token_budget,
                      tool_schema_mode='all', **flags)
        transport = ScriptedTransport(task['calls'], root)
        app = Harness(HarnessConfig(**kwargs), transport=transport, codex_gateway_home=root / '.gateway')
        conversation = app.create_session()
        session_id = conversation.id
        try:
            if task.get('scenario') == 'unknown-write' and mode == 'offline-scripted':
                original = conversation.append
                failed = False

                async def interrupted(kind, data=None, **options):
                    nonlocal failed
                    if kind == 'tool/result' and isinstance(data, dict) and data.get('name') == 'edit' and not failed:
                        failed = True
                        raise OSError('Fixture result persistence interrupted')
                    return await original(kind, data, **options)

                conversation.append = interrupted
            try:
                outcome = await app.run_async(conversation, task['prompt']['en'])
                result['terminal_reason'] = outcome.reason
                if outcome.reason != 'completed' and task.get('scenario') != 'unknown-write':
                    result['errors'].append('Run did not complete: ' + outcome.reason)
            except Exception as exc:
                if task.get('scenario') != 'unknown-write' or mode != 'offline-scripted':
                    raise
                result['injected_interruption'] = type(exc).__name__
            if task.get('scenario') in ('restart', 'unknown-write'):
                if task.get('scenario') == 'unknown-write' and not failed:
                    result['errors'].append('Expected result persistence interruption did not occur')
                if task.get('scenario') == 'unknown-write' and result.get('terminal_reason') != 'error' and 'injected_interruption' not in result:
                    result['errors'].append('Expected interrupted execution did not fail')
                await app.close()
                resumed = ScriptedTransport([{'name': 'read', 'arguments': {'path': 'solution.py'}}], root) if mode == 'offline-scripted' else None
                app = Harness(HarnessConfig(**kwargs), transport=resumed, codex_gateway_home=root / '.gateway')
                conversation = app.get_session(session_id)
                resumed_outcome = await app.run_async(conversation, 'Inspect the existing result. Preserve successful edits and do not replay earlier writes.')
                if resumed_outcome.reason != 'completed':
                    result['errors'].append('Resumed run did not complete: ' + resumed_outcome.reason)
            calls = list(conversation.events_of_type('tool/call'))
            result['tool_calls'] = sum(row.data.get('status') == 'running' for row in calls)
            tool_errors = [row for row in conversation.events_of_type('tool/result') if any(item.get('is_error') for item in row.data.get('results', []))]
            if tool_errors and task.get('scenario') not in ('stale', 'unknown-write'):
                result['errors'].append('Unexpected tool failure')
            if task.get('scenario') == 'stale':
                from harness.session.tool_activity import tool_invocations
                rows = tool_invocations(conversation.events)
                if ([row['name'] for row in rows] != ['read', 'edit', 'read', 'edit']
                        or len(tool_errors) != 1 or not rows[1]['is_error'] or rows[3]['is_error']
                        or 'FS_STALE' not in json.dumps(dict(tool_errors[0].data), default=str)):
                    result['errors'].append('Stale observation was not rejected, reread and successfully merged')
            if task.get('scenario') in ('restart', 'unknown-write'):
                edits = [row for row in calls if row.data.get('status') == 'running' and row.data.get('name') == 'edit']
                if len(edits) != 1:
                    result['errors'].append('Recovery duplicated or lost the original edit')
            passed, error = await independent_check(root, task['checks'])
            result['passed'] = passed and not result['errors']
            if error:
                result['errors'].append(error)
        except Exception as exc:
            result['errors'].append(type(exc).__name__)
        finally:
            await app.close()
    result['elapsed_ms'] = round((time.perf_counter() - started) * 1000)
    return result


async def evaluate_coding_tasks(*, repeat=1, task_ids=None, mode='offline-scripted', token_budget=100000, fixture=FIXTURE):
    """使用同一固定任务合同分别报告脚本回归或显式在线效果。"""
    if type(repeat) is not int or not 1 <= repeat <= 10 or mode not in ('offline-scripted', 'online'):
        raise ValueError('Invalid evaluation options')
    if mode == 'online':
        raise ValueError('Online evaluation is unavailable until an isolated execution provider is verified')
    tasks = load_tasks(fixture)
    selected = set(task_ids or [task['id'] for task in tasks])
    if not selected or selected - {task['id'] for task in tasks}:
        raise ValueError('Unknown coding task')
    results = []
    for repetition in range(1, repeat + 1):
        for task in tasks:
            if task['id'] in selected:
                results.append(await evaluate_task(task, mode=mode, repetition=repetition, token_budget=token_budget))
    per_task = {task_id: [row['passed'] for row in results if row['task_id'] == task_id] for task_id in sorted(selected)}
    return {'mode': mode, 'model_quality_evaluated': False,
            'first_attempt_pass_rate': sum(values[0] for values in per_task.values()) / len(per_task),
            'all_repetitions_pass_rate': sum(all(values) for values in per_task.values()) / len(per_task),
            'fixture_sha256': hashlib.sha256(Path(fixture).read_bytes()).hexdigest(),
            'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'python': platform.python_version(), 'platform': sys.platform, 'token_budget': token_budget,
            'repeat': repeat, 'passed': all(row['passed'] for row in results), 'results': results}


def main():
    """在线调用必须显式选择；默认不读取凭据、不访问外部服务。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('offline-scripted', 'online'), default='offline-scripted')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--task', action='append')
    parser.add_argument('--token-budget', type=int, default=100000)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate_coding_tasks(repeat=args.repeat, task_ids=args.task, mode=args.mode, token_budget=args.token_budget))
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    else:
        print(text)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
