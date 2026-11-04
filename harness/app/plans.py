"""持久化审核版本，独立于可压缩的消息表面。"""
from __future__ import annotations

from harness.i18n import tr

import uuid
import hashlib
from collections.abc import Mapping
from typing import Any

from ..core.diff import unified_patch
from .plan_mode import first_heading


def content_hash(plan: str) -> str:
    """返回计划正文的稳定 SHA-256 摘要，用于审批与恢复时核对版本。"""
    return hashlib.sha256(plan.encode('utf-8')).hexdigest()


def versions(session: Any) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if session is None:
        return []
    for event in session.events_of_type("note"):
        data = event.data
        if data.get("kind") == "plan_submission":
            plan = data["plan"]
            digest = data.get("content_hash")
            if not isinstance(digest, str) or len(digest) != 64:
                # 旧事件没有摘要时只派生只读摘要，不把它伪装成提交时的字段。
                digest = content_hash(plan)
            records[data["id"]] = {"id": data["id"], "title": data["title"], "plan": plan,
                "content_hash": digest,
                "submitted_at": event.time, "seq": event.seq, "status": "pending", "feedback": "",
                "previous_id": data.get("previous_id"), "decided_at": None}
        elif data.get("kind") == "plan_decision" and data.get("id") in records:
            records[data["id"]].update(status=data["status"], feedback=data.get("feedback", ""), decided_at=event.time)
    return list(records.values())


async def submit(session: Any, plan: str, call_id: str) -> str:
    prior = versions(session)
    plan_id = "plan_" + uuid.uuid4().hex
    # 先提交新版本，再关闭旧待审版本。新提交失败时旧审批仍有效，
    # 不把一次未知的持久化失败误报成“旧计划已被替换”。
    await session.append("note", {"kind": "plan_submission", "id": plan_id, "title": first_heading(plan)[:120],
        "plan": plan, "content_hash": content_hash(plan), "call_id": call_id,
        "previous_id": prior[-1]["id"] if prior else None})
    # 后续提交替换仍待审核的版本，不继承其审批。
    for record in prior:
        if record["status"] == "pending":
            await decide(session, record["id"], "superseded")
    return plan_id


async def decide(session: Any, plan_id: str, status: str, feedback: str = "") -> None:
    await session.append("note", {"kind": "plan_decision", "id": plan_id, "status": status, "feedback": feedback})


def summaries(session: Any) -> list[dict[str, Any]]:
    return [{key: value for key, value in record.items() if key != "plan"} for record in versions(session)]


def implementation(session: Any, plan_id: str) -> list[dict[str, Any]]:
    return next((list(event.data.get('todos', [])) for event in reversed(session.events_of_type('todo/write'))
                 if event.data.get('plan_id') == plan_id), [])


def detail(session: Any, plan_id: str) -> dict[str, Any] | None:
    records = versions(session)
    record = next((item for item in records if item["id"] == plan_id), None)
    if record is None:
        return None
    previous = next((item for item in records if item["id"] == record["previous_id"]), None)
    diff = "" if previous is None else unified_patch(
        previous["plan"], record["plan"], fromfile="previous-plan.md", tofile="plan.md")
    return {**record, "diff": diff, 'implementation': implementation(session, plan_id)}


def evidence_page(session: Any, plan_id: str, source_seq: int, *, offset: int = 0, limit: int = 20000) -> dict[str, Any] | None:
    """分页读取此计划引用的确切已提交结果，限制单页大小。"""
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 20000:
        raise ValueError(tr('python.plans.5b3276a1cc7f'))
    if not any(record['id'] == plan_id for record in versions(session)):
        return None
    reference = next((entry for item in implementation(session, plan_id) for entry in item.get('evidence', [])
                      if entry.get('source_seq') == source_seq), None)
    event = session.event_at(source_seq) if reference else None
    if event is None or event.type != 'tool/result' or event.data.get('call_id') != reference['call_id'] or event.data.get('name', '') != reference['name']:
        return None
    results = event.data.get('results', [])
    if not results or any(not isinstance(row, Mapping) or row.get('is_error') is not False or row.get('tool_use_id') != reference['call_id'] for row in results):
        return None
    text = '\n'.join(str(row.get('content', '')) for row in results)
    if offset > len(text):
        raise ValueError(tr('python.plans.781561d7ff5a'))
    end = min(offset + limit, len(text))
    return {'call_id': reference['call_id'], 'name': reference['name'], 'source_seq': event.seq,
            'offset': offset, 'text': text[offset:end], 'total_chars': len(text),
            'next_offset': end if end < len(text) else None}


def approved_context(session: Any) -> str:
    approved = [record for record in versions(session) if record["status"] == "approved"]
    if not approved:
        return ""
    record = approved[-1]
    tasks = implementation(session, record['id'])
    progress = '\n'.join(f"{item['id']}: {item['status']}: {item['content']}" for item in tasks)
    progress_context = (tr('owned.plans.5e3c0d3daec1') + progress[:5000]
                        + (tr('owned.plans.7139bc48913d') if len(progress) > 5000 else '')) if tasks else ''
    return tr('prompt.approved_plan', version=record['id'], plan=record['plan']) + progress_context
