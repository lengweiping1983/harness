"""文件搜索的机器错误协议；独立 worker 仅依赖标准库。"""
from __future__ import annotations


class SearchError(ValueError):
    """只保存文案键和原始参数，由宿主在回合语言中渲染。"""

    def __init__(self, message_key: str, *, code: str, message_values: dict | None = None, **details):
        super().__init__(message_key)
        self.message_key = message_key
        self.message_values = message_values or {}
        self.code = code
        self.details = details

    def to_dict(self) -> dict:
        """返回可跨进程传递的结构，不在 worker 中加载产品资源。"""
        return {'code': self.code, 'message_key': self.message_key,
                'message_values': self.message_values, 'details': self.details}
