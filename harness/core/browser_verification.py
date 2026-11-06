"""浏览器开发交付前的有限复核，只记录实际工具事实，不代替业务验收。"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import PurePath
from typing import Any

from harness.i18n import tr


class BrowserVerificationReminder:
    """每轮独立保存检查代次；最多提醒一次，不接管页面或重放工具。"""

    def __init__(self) -> None:
        self.change_seq: int | None = None
        self.html_change_seq: int | None = None
        self.browser_result: dict[str, Any] | None = None
        self.screenshot: dict[str, Any] | None = None
        self.diagnostics: dict[str, Any] | None = None
        self.health: dict[str, int] | None = None
        self.owner: str | None = None
        self.page_id: str | None = None
        self.failure_seq: int = -1
        self.input_seq: int = -1
        self.checked = False

    def observe(self, call: Any, outcome: Any, *, source_seq: int) -> None:
        """仅消费已提交结果，不从页面正文、任务名称或错误文本猜状态。"""
        if call.name in {'write', 'edit'} and not outcome.is_error:
            display = outcome.display
            if isinstance(display, Mapping) and display.get('changed') is False:
                return
            self.change_seq = source_seq
            path = call.input.get('path') if isinstance(call.input, Mapping) else None
            if isinstance(path, str) and PurePath(path).suffix.casefold() in {'.html', '.htm'}:
                self.html_change_seq = source_seq
        if call.name != 'browser':
            return
        action = call.input.get('action') if isinstance(call.input, Mapping) else None
        if not isinstance(action, str):
            action = None
        if action not in {'observe', 'options', 'screenshot', 'tabs', 'diagnostics'}:
            self.input_seq = source_seq
        # 动作来自公开协议，不把第三方页面内容提升为开发者指令。
        self.browser_result = {'source_seq': source_seq, 'is_error': bool(outcome.is_error)}
        if outcome.is_error:
            self.failure_seq = source_seq
        data = outcome.data if isinstance(outcome.data, Mapping) else {}
        if 'owner' in data:
            owner = data['owner']
            self.owner = owner if isinstance(owner, str) and owner in {'agent', 'user'} else None
        health = data.get('health')
        if isinstance(health, Mapping):
            self.health = self._counts(health)
        page = data.get('page_id')
        if outcome.is_error or not isinstance(page, str) or not page or len(page) > 80:
            return
        self.page_id = page
        evidence = {'source_seq': source_seq, 'page_id': page}
        if action == 'screenshot':
            self.screenshot = evidence
        elif action == 'diagnostics':
            summary = data.get('summary')
            if isinstance(summary, Mapping):
                self.health = self._counts(summary)
                self.diagnostics = {**evidence, **self.health}

    @staticmethod
    def _counts(summary: Mapping) -> dict[str, int]:
        """只提取真实计数，不把页面正文或布尔值当成健康证据。"""
        return {key: summary[key] for key in ('errors', 'warnings', 'dropped')
                if type(summary.get(key)) is int and summary[key] >= 0}

    def take_notice(self) -> dict[str, Any] | None:
        """清单全勾选也复核最新代码；缺证据只提醒，不补造通过或强行继续。"""
        if (self.checked or self.change_seq is None
                or self.browser_result is None and self.html_change_seq is None):
            return None
        screenshot, diagnostics = self.screenshot, self.diagnostics
        current_checks = bool(screenshot and diagnostics
            and screenshot['source_seq'] > max(self.change_seq, self.failure_seq, self.input_seq)
            and diagnostics['source_seq'] > max(self.change_seq, self.failure_seq, self.input_seq)
            and screenshot['page_id'] == diagnostics['page_id'] == self.page_id)
        healthy = bool(diagnostics and diagnostics.get('errors') == 0
                       and diagnostics.get('warnings') == 0 and diagnostics.get('dropped') == 0)
        if self.health is not None:
            healthy = healthy and all(self.health.get(key) == 0 for key in ('errors', 'warnings', 'dropped'))
        if (current_checks and healthy and self.browser_result is not None
                and not self.browser_result['is_error'] and self.owner == 'agent'):
            return None
        self.checked = True
        facts = {'last_file_change_seq': self.change_seq, 'last_browser_result': self.browser_result,
                 'last_html_change_seq': self.html_change_seq,
                 'last_browser_failure_seq': self.failure_seq if self.failure_seq >= 0 else None,
                 'last_browser_input_seq': self.input_seq if self.input_seq >= 0 else None,
                 'screenshot': screenshot, 'diagnostics': diagnostics, 'owner': self.owner,
                 'page_id': self.page_id, 'health': self.health}
        return {'kind': 'browser_final_review',
                'text': tr('browser_verification.final_review') + '\n' + json.dumps(facts, ensure_ascii=False)}
