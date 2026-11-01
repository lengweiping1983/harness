"""临时页面诊断，不采集响应正文、请求头或控制台参数。"""
from __future__ import annotations

from harness.i18n import tr

import time
from collections import deque
from urllib.parse import urlsplit, urlunsplit

from ..core.errors import HarnessError


def diagnostic_url(value: str) -> str:
    """不暴露内嵌凭据、查询令牌、片段或数据地址。"""
    try:
        parsed = urlsplit(str(value))
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            return ''
        return urlunsplit((parsed.scheme, parsed.netloc.rsplit('@', 1)[-1], parsed.path, '', ''))[:1000]
    except ValueError:
        return ''


class PageDiagnostics:
    def __init__(self):
        self.records: deque[dict] = deque(maxlen=100)
        self.revision = 0
        self.dropped = 0

    def add(self, kind: str, message: str, *, level: str = 'error', url: str = '', line: int | None = None,
            status: int | None = None, resource_type: str = ''):
        self.revision += 1
        if len(self.records) == self.records.maxlen:
            self.dropped += 1
        record = {'id': self.revision, 'kind': kind, 'level': level, 'message': str(message)[:2000],
                  'url': diagnostic_url(url), 'timestamp': time.time()}
        if line is not None:
            record['line'] = line
        if status is not None:
            record['status'] = status
        if resource_type:
            record['resource_type'] = str(resource_type)[:40]
        self.records.append(record)

    def summary(self):
        return {'errors': sum(row['level'] == 'error' for row in self.records),
                'warnings': sum(row['level'] == 'warning' for row in self.records),
                'total': len(self.records), 'dropped': self.dropped}

    def view(self, limit: int = 100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise HarnessError(tr('python.browser_diagnostics.4128d0231a96'), code="browser/diagnostics-invalid")
        return {'revision': self.revision, 'summary': self.summary(), 'records': list(reversed(self.records))[:limit],
                'records_omitted': max(0, len(self.records) - limit),
                'page_content_trusted': False}

    def clear(self, expected_revision: int):
        if type(expected_revision) is not int:
            raise HarnessError(tr('python.browser_diagnostics.714c46543256'), code='browser/diagnostics-invalid')
        if self.revision != expected_revision:
            raise HarnessError(tr('python.browser_diagnostics.e834138f0c21'), code='browser/diagnostics-changed')
        self.revision += 1
        self.records.clear()
        self.dropped = 0
