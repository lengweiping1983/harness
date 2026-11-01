"""精选连接器预设（curated presets）。

连接器预设给出服务的
声明（传输、端点、凭据引用）与配套技能，用户一键安装后即出现在
连接器目录里，随后走既有的 绑定 → 启用 生命周期。

安全纪律与 :mod:`harness.connectors.store` 一致：凭据只以
``${VAR}`` 引用形式落盘，真实令牌在连接时从进程环境插值，
永远不会写入 ``connectors.json``；OAuth 令牌则直接进系统凭据库
（:mod:`harness.connectors.credentials`，支持受限文件后端），不写入连接器声明。

目前内置预设：

* ``github`` —— GitHub 官方远程 MCP（streamable-http），覆盖
  仓库、Issue、Pull Request、Actions、代码搜索等能力；
  同时支持 PAT（``${GITHUB_TOKEN}`` 引用）与 OAuth 浏览器授权
  （``metadata.oauth`` 声明，client_id 由 ``GITHUB_CLIENT_ID`` 提供）。
* ``context7`` —— Upstash Context7 官方远程 MCP（streamable-http，
  免鉴权），为模型提供各库/框架的最新文档与代码示例；
* ``deepwiki`` —— Devngraph DeepWiki 官方远程 MCP（streamable-http，
  免鉴权），AI 生成的 GitHub 仓库百科（结构 / 全文 / 提问）；
* ``playwright`` —— Microsoft Playwright MCP（stdio，``npx`` 拉起），
  提供浏览器自动化（导航 / 点击 / 填表 / 截图 / 快照）。

远端预设的端点均以 MCP ``initialize`` 探测核实（2026-10）。
"""

from __future__ import annotations

from harness.i18n import tr

from dataclasses import dataclass, replace
from copy import deepcopy

from ..core.errors import ConnectorError
from .store import ConnectorRecord

#: GitHub 官方远程 MCP 端点
GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"

#: Context7 官方远程 MCP 端点（免鉴权；可选 CONTEXT7_API_KEY 提高限额）
CONTEXT7_MCP_URL = "https://mcp.context7.com/mcp"

#: DeepWiki 官方远程 MCP 端点（免鉴权）
DEEPWIKI_MCP_URL = "https://mcp.deepwiki.com/mcp"

#: 随预设附带的配套技能（用户作用域，已存在则不覆盖）
GITHUB_SKILL_NAME = "github"

GITHUB_SKILL_KEY = 'python.presets.2097ab1adb23'


@dataclass(frozen=True, slots=True)
class ConnectorPreset:
    """一个精选连接器预设：声明 + 可选的配套技能。"""

    record: ConnectorRecord
    #: 配套技能名（``None`` 表示无附带技能）
    skill_name: str | None = None
    #: 配套技能的 SKILL.md 全文（安装时写入用户技能目录）
    skill_markdown: str | None = None
    #: 内置资源身份；未声明时保留调用方原文。
    description_key: str | None = None
    auth_hint_key: str | None = None
    skill_key: str | None = None

    def resolve(self) -> ConnectorPreset:
        """按本次语言构建独立记录，不共享可变安装参数。"""
        record = deepcopy(self.record)
        for field, key in (('description', self.description_key), ('auth_hint', self.auth_hint_key)):
            if key is not None:
                record.metadata[field] = tr(key)
        return replace(self, record=record,
                       skill_markdown=tr(self.skill_key) if self.skill_key is not None else self.skill_markdown)

    def summary(self) -> dict[str, object]:
        """可 JSON 化的预设摘要（供 UI 渲染推荐卡片）。"""
        metadata = self.record.metadata
        return {
            "name": self.record.name,
            "transport": self.record.transport,
            "url": self.record.url,
            "description": tr(self.description_key) if self.description_key is not None else str(metadata.get("description", "")),
            "homepage": str(metadata.get("homepage", "")),
            "auth_hint": tr(self.auth_hint_key) if self.auth_hint_key is not None else str(metadata.get("auth_hint", "")),
            "has_skill": bool(self.skill_key or self.skill_markdown),
        }


PRESETS: dict[str, ConnectorPreset] = {
    "github": ConnectorPreset(
        record=ConnectorRecord(
            name="github",
            transport="streamable-http",
            url=GITHUB_MCP_URL,
            headers={"Authorization": "Bearer ${GITHUB_TOKEN}"},
            enabled=True,
            bound=False,
            metadata={
                "homepage": "https://github.com/github/github-mcp-server",
                "preset": "github",
                "oauth": {
                    "client_id_env": "GITHUB_CLIENT_ID",
                    # 留空表示取受保护资源元数据声明的全部 scope
                    "scopes": [],
                },
            },
        ),
        skill_name=GITHUB_SKILL_NAME,
        skill_key=GITHUB_SKILL_KEY,
        description_key="python.presets.ea932f93ed9d",
        auth_hint_key="python.presets.24627b103dfe",
    ),
    "context7": ConnectorPreset(
        record=ConnectorRecord(
            name="context7",
            transport="streamable-http",
            url=CONTEXT7_MCP_URL,
            enabled=True,
            bound=False,
            metadata={
                "homepage": "https://context7.com",
                "preset": "context7",
            },
        ),
        description_key="python.presets.356a01dd3ac2",
        auth_hint_key="python.presets.445100bf81e1",
    ),
    "deepwiki": ConnectorPreset(
        record=ConnectorRecord(
            name="deepwiki",
            transport="streamable-http",
            url=DEEPWIKI_MCP_URL,
            enabled=True,
            bound=False,
            metadata={
                "homepage": "https://deepwiki.com",
                "preset": "deepwiki",
            },
        ),
        description_key="python.presets.5f3e999df73d",
        auth_hint_key="python.presets.d51185fc8d58",
    ),
    "playwright": ConnectorPreset(
        record=ConnectorRecord(
            name="playwright",
            transport="stdio",
            command="npx",
            args=["@playwright/mcp@latest"],
            enabled=True,
            bound=False,
            metadata={
                "homepage": "https://github.com/microsoft/playwright-mcp",
                "preset": "playwright",
            },
        ),
        description_key="python.presets.991cb78a3ea8",
        auth_hint_key="python.presets.af187a2c85ee",
    ),
}


def get_preset(name: str) -> ConnectorPreset:
    """按名取预设。

    :raises ConnectorError: 预设不存在（消息里列出可用预设）。
    """
    preset = PRESETS.get(name)
    if preset is None:
        available = ", ".join(sorted(PRESETS)) or tr('python.presets.f4b39d9b4542')
        raise ConnectorError(tr('python.presets.94d07d993510', p0=f'{name!r}', p1=f'{available}'))
    return preset.resolve()


__all__ = [
    "ConnectorPreset",
    "PRESETS",
    "get_preset",
    "GITHUB_MCP_URL",
    "GITHUB_SKILL_NAME",
    "GITHUB_SKILL_KEY",
    "CONTEXT7_MCP_URL",
    "DEEPWIKI_MCP_URL",
]
