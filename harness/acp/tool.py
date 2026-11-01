"""通过现有工具执行链委派独立 Agent，不向模型开放任意启动命令。"""
from __future__ import annotations

import time
from pathlib import Path

from ..i18n import tr
from ..llm.types import TextBlock
from ..tools.registry import ToolDefinition, ToolResult
from .client import AcpClient, AcpError
from .runners import PRESETS, discover_runners, runner_config


def build_external_agent_tools(harness):
    """检测为只读操作；委派要求宿主同时开启文件与命令能力。"""
    tools = [ToolDefinition({
        'name': 'list_external_agents',
        'description': tr('acp.tool.discover_description'), 'description_key': 'acp.tool.discover_description',
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
    }, lambda args, ctx: {'runners': discover_runners()}, effect='read', category='agent', idempotent=True)]
    if not harness.config.enable_shell or not harness.config.enable_fs:
        return tools

    async def execute(args, ctx):
        preset = next(item for item in PRESETS if item.id == args['agent'])
        row = next(item for item in discover_runners() if item['id'] == preset.id)
        if not row['available']:
            return ToolResult([TextBlock(tr('acp.tool.missing', agent=preset.title, command=preset.install))], is_error=True)
        state = harness.permissions.snapshot(ctx.session_id)
        if state['permission_policy'] == 'read_only':
            return ToolResult([TextBlock(tr('acp.tool.read_only'))], is_error=True)
        stdout = stderr = ''
        dropped = 0
        denied = False
        last_report = 0.0

        async def report(force=False):
            nonlocal last_report
            if force or time.monotonic() - last_report >= .1:
                last_report = time.monotonic()
                await ctx.report_output(stdout, stderr, stdout_dropped=dropped)

        async def update(params):
            nonlocal stdout, stderr, dropped
            change = params.get('update', {})
            if change.get('sessionUpdate') == 'agent_message_chunk':
                content = change.get('content', {})
                if content.get('type') == 'text' and isinstance(content.get('text'), str):
                    combined = stdout + content['text']
                    dropped += max(0, len(combined) - 32768)
                    stdout = combined[-32768:]
            elif change.get('sessionUpdate') in {'tool_call', 'tool_call_update'}:
                title = change.get('title', '')
                status = change.get('status', '')
                if isinstance(title, str) and isinstance(status, str) and (title or status):
                    stderr = (stderr + f'{title[:500]} {status[:100]}\n')[-32768:]
            await report()

        async def permission(params):
            nonlocal denied
            allowed = False
            try:
                # 再检查计划和本次调用授权，避免等待外部请求期间权限扩大。
                reason = harness.plan.guard(definition, args, ctx)
                reason = reason or next((value for check in ctx.admission_checks if (value := check())), None)
                selected = None if reason else await harness.permissions.external_agent_permission(ctx, preset.title, params)
                reason = harness.plan.guard(definition, args, ctx)
                reason = reason or next((value for check in ctx.admission_checks if (value := check())), None)
                if reason:
                    selected = None
                option = next((item for item in params.get('options', []) if isinstance(item, dict) and item.get('optionId') == selected), {})
                allowed = option.get('kind') == 'allow_once'
                return selected
            finally:
                # 回调异常也会让协议客户端取消请求，不能把 end_turn 当作完整成功。
                if not allowed:
                    denied = True

        async def run():
            client = AcpClient(runner_config(preset.id, command=row['command_path']), on_update=update, on_permission=permission)
            try:
                directory = Path(ctx.cwd or harness.config.cwd or Path.cwd()).resolve(strict=True)
                await client.start(directory)
                session_id = await client.new_session(directory)
                response = await client.prompt(session_id, args['task'])
                reason = response.get('stopReason', 'unknown')
                complete = reason == 'end_turn' and not denied
                result = {'agent': preset.id, 'session_id': session_id, 'stop_reason': reason,
                          'permission_denied': denied, 'stdout': stdout, 'stdout_dropped': dropped,
                          'stderr': stderr, 'completed': complete}
                text = stdout or tr('acp.tool.no_text')
                if dropped:
                    text = tr('acp.tool.truncated', count=dropped) + '\n' + text
                if not complete:
                    text = tr('acp.tool.incomplete', reason=reason) + '\n' + text
                return ToolResult([TextBlock(text)], is_error=not complete, data=result, display=result)
            except (AcpError, OSError, ValueError, TimeoutError) as exc:
                return ToolResult([TextBlock(tr('acp.tool.failed', error=str(exc) or type(exc).__name__) + '\n' + stdout)], is_error=True)
            finally:
                await client.close()
                await report(force=True)

        return await harness.permissions.run_external_agent(run(), state)

    definition = ToolDefinition({
        'name': 'delegate_agent',
        'description': (tr('acp.tool.delegate_description')), 'description_key': 'acp.tool.delegate_description',
        'parameters': {'type': 'object', 'properties': {
            'agent': {'type': 'string', 'enum': [item.id for item in PRESETS]},
            'task': {'type': 'string', 'minLength': 1, 'maxLength': 64000},
        }, 'required': ['agent', 'task'], 'additionalProperties': False},
    }, execute, effect='execute', category='agent', concurrency_safe=False, timeout=1800,
        approval_required=True, cancellable=True, execution_cwd=lambda args, ctx: ctx.cwd)
    tools.append(definition)
    return tools
