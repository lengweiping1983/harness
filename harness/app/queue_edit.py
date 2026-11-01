"""修改前比较排队输入快照，不依赖运行时。"""
from __future__ import annotations

from harness.i18n import tr

import hashlib
import json
from typing import Any

from ..core.errors import HarnessError


def queue_version(item_id: str, text: str) -> str:
    return hashlib.sha256(json.dumps([item_id, text], ensure_ascii=False).encode()).hexdigest()


def check_queue_version(item_id: str, text: str, expected: str | None, *, display_text: str | None = None) -> None:
    # 现有 SDK 调用方可选择启用比较，限于会话的 HTTP 修改必须启用。
    if expected is None:
        return
    if not isinstance(expected, str) or len(expected) != 64:
        raise HarnessError(tr('python.queue_edit.1f4e59c2c94a'), code="run/invalid")
    if expected != queue_version(item_id, text):
        raise HarnessError(tr('python.queue_edit.abc7ff6e3320'), code="run/queue-changed",
                           current={"id": item_id, "text": text if display_text is None else display_text, "edit_version": queue_version(item_id, text)})


def queued_view(record: Any) -> dict[str, Any]:
    return {**record.to_dict(), "text": (record.prompt_input or {}).get("text", record.text), "edit_version": queue_version(record.id, record.text),
            "attachment_refs": (record.prompt_input or {}).get("attachment_refs", [])}

def check_queue_order(current: list[dict[str, Any]], expected: Any, order: Any) -> list[str]:
    """完整快照比较防止新任务、已启动任务或其他标签页的排序被覆盖。"""
    if not isinstance(expected, list) or not isinstance(order, list) or not all(isinstance(key, str) for key in order):
        raise HarnessError(tr('queue.order.invalid'), code='run/invalid')
    snapshot = [{'id': row['id'], 'edit_version': row['edit_version']} for row in current]
    if expected != snapshot:
        raise HarnessError(tr('queue.order.changed'), code='run/queue-order-changed')
    ids = [row['id'] for row in current]
    if len(order) != len(ids) or len(set(order)) != len(order) or set(order) != set(ids):
        raise HarnessError(tr('queue.order.invalid'), code='run/invalid')
    return order
