"""MCP 服务准备错误保留资源身份，跨线程保存后仍按读取语言展示。"""
from ..i18n import tr


class McpServiceError(RuntimeError):
    """仅用于宿主拥有的提示；第三方诊断不按原文反向匹配。"""

    def __init__(self, key: str, **values):
        self.key, self.values = key, values
        super().__init__(key)

    def __str__(self):
        return tr(self.key, **self.values)
