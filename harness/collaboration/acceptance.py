"""独立于调度策略的协作验收：声明、真实证据关联与统一判定。"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping

from ..i18n import tr
from typing import Any

PASSED = frozenset({'ok', 'pass', 'passed', 'success', 'succeeded', 'done', 'completed'})
FAILED = frozenset({'fail', 'failed', 'failure', 'error', 'blocked', 'rejected'})
COMMANDS = frozenset({'bash', 'pwsh', 'shell', 'exec_command', 'run_code'})


def evaluate_report(payload: Any, ok: bool, *, session: Any = None) -> dict[str, Any]:
    """工具关联只证明执行或观察发生且成功，不替代检查标准的独立审阅。"""
    def result(status, reason, failed=(), refs=()):
        return {'status': status, 'failed_checks': list(failed), 'reason': reason, 'verified_tool_calls': list(refs)}
    if not ok or not isinstance(payload, Mapping):
        return result('unverified', 'missing_report')
    status = payload.get('status')
    status = status.strip().lower() if isinstance(status, str) else ''
    checks = payload.get('checks')
    failed = [(row.get('name')[:200] if isinstance(row.get('name'), str) and row['name'].strip() else tr('common.unnamed')) for row in checks if isinstance(row, Mapping) and (row.get('passed') is False or row.get('ok') is False)] if isinstance(checks, list) else []
    if failed or status in FAILED:
        return result('failed', 'reported_failure', failed)
    if status not in PASSED or not isinstance(checks, list) or not 1 <= len(checks) <= 32:
        return result('unverified', 'missing_checks')
    evidence = payload.get('evidence')
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 32 or any(not isinstance(text, str) or not text.strip() or len(text) > 4000 for text in evidence):
        return result('unverified', 'missing_evidence')
    for row in checks:
        if not isinstance(row, Mapping) or not isinstance(row.get('name'), str) or not row['name'].strip() or len(row['name']) > 200 or row.get('passed') is not True or not isinstance(row.get('detail'), str) or not row['detail'].strip() or len(row['detail']) > 4000 or row.get('kind') not in ('command', 'observation') or not isinstance(row.get('tool_call_id'), str) or not row['tool_call_id'] or len(row['tool_call_id']) > 200:
            return result('unverified', 'invalid_check')
    if session is None:
        return result('unverified', 'missing_runtime_evidence')
    tools, duplicates = {}, set()
    for event in session.events_of_type('tool/result'):
        if event.data.get('turn') != session.current_turn:
            continue
        identifier = event.data.get('call_id')
        if identifier in tools:
            duplicates.add(identifier)
        tools[identifier] = event
    refs = []
    for row in checks:
        # 仅有调用 ID 的报告无法区分同回合复用，不能以后一次成功覆盖先前失败。
        if row['tool_call_id'] in duplicates:
            return result('unverified', 'ambiguous_tool_evidence')
        event = tools.get(row['tool_call_id'])
        if event is None:
            return result('unverified', 'unknown_or_stale_tool')
        if event.data.get('name') == 'acceptance_evidence':
            return result('unverified', 'inventory_is_not_evidence')
        outcomes = event.data.get('results')
        display = event.data.get('display') or {}
        if not isinstance(outcomes, (list, tuple)) or not outcomes or any(not isinstance(item, Mapping) or item.get('is_error') is not False for item in outcomes):
            return result('failed', 'tool_failed', [row['name']])
        if not isinstance(display, Mapping):
            display = {}
        if display.get('timed_out') or display.get('not_executed') or display.get('interrupted') or display.get('execution_state') == 'not-started' or type(display.get('exit_code')) is int and display['exit_code'] != 0:
            return result('failed', 'tool_failed', [row['name']])
        # 终端等待只代表输出窗口结束，不证明命令退出；后台结果须引用完成回执。
        if display.get('background') or event.data.get('name') == 'terminal_send':
            return result('unverified', 'command_not_completed')
        if event.data.get('name') == 'job_output':
            job = display.get('job')
            if not isinstance(job, Mapping) or job.get('kind') == 'pty-send' or job.get('completionScope') == 'terminal-wait' or job.get('archived'):
                return result('unverified', 'command_not_completed')
            if job.get('status') in ('failed', 'killed') or type(job.get('exitCode')) is int and job['exitCode'] != 0:
                return result('failed', 'tool_failed', [row['name']])
            if job.get('status') != 'completed' or type(job.get('exitCode')) is not int or job['exitCode'] != 0:
                return result('unverified', 'command_not_completed')
            refs.append({'call_id': row['tool_call_id'], 'result_seq': event.seq, 'kind': row['kind']})
            continue
        # 命令证据按真实工具类型校验，不能通过自报为观察绕过完成状态。
        if (row['kind'] == 'command' or event.data.get('name') in COMMANDS) and (event.data.get('name') not in COMMANDS or type(display.get('exit_code')) is not int or display['exit_code'] != 0 or display.get('background')):
            return result('unverified', 'command_not_completed')
        refs.append({'call_id': row['tool_call_id'], 'result_seq': event.seq, 'kind': row['kind']})
    return result('passed', 'runtime_evidence_linked', refs=refs)


def build_acceptance_evidence_tool():
    """模型可查看本回合真实调用 ID，不必猜测供应商分配的标识。"""
    from ..tools.registry import ToolDefinition, ToolResult
    from ..llm.types import TextBlock
    from ..core.errors import ToolDeniedError
    def execute(args, context):
        session = context.services.get('session')
        if session is None or session.id != context.session_id:
            raise ToolDeniedError(tr('agent.team.evidence_unavailable'))
        rows = []
        for event in session.events_of_type('tool/result'):
            if event.data.get('turn') != context.turn or event.data.get('name') == 'acceptance_evidence':
                continue
            display = event.data.get('display') or {}
            rows.append({'tool_call_id':event.data.get('call_id'), 'result_seq':event.seq, 'name':event.data.get('name'),
                'is_error':any(not isinstance(result, Mapping) or result.get('is_error') is not False for result in (event.data.get('results') or ())),
                'execution':{key:display[key] for key in ('exit_code','execution_state','timed_out','background','not_executed','interrupted') if isinstance(display, Mapping) and key in display}})
        value = {'turn': context.turn, 'records': rows[-50:], 'truncated':len(rows)>50}
        return ToolResult([TextBlock(json.dumps(value,ensure_ascii=False))], data=value)
    return ToolDefinition({'name':'acceptance_evidence','description':tr('agent.team.evidence_tool'), 'description_key': 'agent.team.evidence_tool',
        'parameters':{'type':'object','properties':{},'additionalProperties':False}}, execute, effect='read', category='session')


_REPORT_BLOCK = re.compile(r"```(?:json)?[ \t]*\r?\n(.*?)```", re.DOTALL)

def acceptance_example() -> str:
    """读取本次请求语言的示例，字段名和机器枚举保持原样。"""
    return tr('agent.team.acceptance_example')


def parse_report(text: str) -> dict[str, Any] | None:
    """从输出末尾查找结构化声明，非法或缺失时保持未验证。"""
    for match in reversed(list(_REPORT_BLOCK.finditer(text or ''))):
        raw = match.group(1).strip()
        if not raw.startswith('{'):
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and 'status' in payload:
            return payload
    return None


def requires_acceptance(metadata: Mapping[str, Any]) -> bool:
    """严格验收由配置决定，不能由执行策略或成员自述关闭。"""
    return str(metadata.get('acceptance', '')).strip().lower() == 'strict'


def acceptance_verdict(required: Iterable[str], members: Mapping[str, Any], *, enforced: bool) -> dict[str, Any]:
    """只汇总实际证据判定；缺席或缺失报告的必需成员仍为未验证。"""
    failed, unverified = [], []
    for name in dict.fromkeys(required):
        status = ((members.get(name) or {}).get('acceptance') or {}).get('status')
        if status != 'passed':
            (failed if status == 'failed' else unverified).append(name)
    return {'passed': not failed and not unverified, 'failed': failed,
            'unverified': unverified, 'enforced': enforced}
