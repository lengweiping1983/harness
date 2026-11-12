"""外部 Agent 预设与无副作用的本机可用性检查。"""
from __future__ import annotations

import shutil
from dataclasses import dataclass

from ..i18n import tr
from .client import AcpRunnerConfig


@dataclass(frozen=True)
class RunnerPreset:
    """适配器与底层 CLI 分别检测，避免误报已经支持 ACP。"""

    id: str
    title: str
    command: str
    args: tuple[str, ...]
    cli: str
    install: str
    docs_url: str


PRESETS = (
    RunnerPreset('opencode', 'OpenCode', 'opencode', ('acp',), 'opencode',
                 'npm install -g opencode-ai', 'https://docs.opencode.ai/docs/cli/'),
    RunnerPreset('codex', 'Codex', 'codex-acp', (), 'codex',
                 'npm install -g @agentclientprotocol/codex-acp',
                 'https://github.com/agentclientprotocol/codex-acp'),
    RunnerPreset('claude', 'Claude Code', 'claude-agent-acp', (), 'claude',
                 'npm install -g @agentclientprotocol/claude-agent-acp',
                 'https://github.com/agentclientprotocol/claude-agent-acp'),
)


def discover_runners() -> list[dict]:
    """只搜索 PATH，不执行 CLI、读取登录凭据或自动下载适配器。"""
    return [{'id': preset.id, 'title': preset.title, 'command': preset.command,
             'args': list(preset.args), 'command_path': shutil.which(preset.command),
             'cli_path': shutil.which(preset.cli), 'available': shutil.which(preset.command) is not None,
             'authentication': 'not_checked', 'install': preset.install, 'docs_url': preset.docs_url}
            for preset in PRESETS]


def runner_config(runner: str, *, command: str | None = None, args: list[str] | None = None,
                  timeout: float = 600) -> AcpRunnerConfig:
    """预设可显式覆盖启动命令，自定义 Agent 必须提供可执行文件。"""
    preset = next((item for item in PRESETS if item.id == runner), None)
    if preset is None and runner != 'custom':
        raise ValueError(tr('acp.runner.unknown', runner=runner))
    executable = command if command is not None else preset.command if preset else ''
    arguments = tuple(args) if args is not None else preset.args if preset else ()
    return AcpRunnerConfig(executable, arguments, request_timeout=timeout)
