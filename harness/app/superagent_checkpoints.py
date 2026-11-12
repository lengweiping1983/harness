"""工作节点的阶段交接协议；模型报告不能代替原运行与冻结文件事实。"""
from __future__ import annotations

from .superagent_common import _error, _text, _json as strict_loads
from .superagent_coordinator_store import _hash, _json


def validate_checkpoint(value):
    """显式就绪才允许解锁依赖，继续和阻塞都不能伪装为完成。"""
    if (not isinstance(value, dict) or set(value) != {'status', 'summary', 'next_steps'}
            or value['status'] not in ('ready', 'continue', 'blocked')
            or not isinstance(value['next_steps'], list) or len(value['next_steps']) > 16):
        raise ValueError
    _text(value['summary'], 'checkpoint.summary', limit=8192)
    for item in value['next_steps']:
        _text(item, 'checkpoint.next_steps', limit=2048)
    if bool(value['next_steps']) != (value['status'] == 'continue'):
        raise ValueError
    return value


def frozen_work_checkpoint(db, assignment):
    """从冻结报告重算检查点，并关联原 Run 证据和同次冻结产物。"""
    try:
        report = assignment["report_json"]
        if isinstance(report, str):
            report = strict_loads(report)
        raw = report["checkpoint_output"]
        _text(raw, "checkpoint", limit=65536)
        checkpoint = validate_checkpoint(strict_loads(raw))
        if (report["kind"] != "work_artifact_report"
                or report["assignment_id"] != assignment["assignment_id"]
                or report["run_id"] != assignment["run_id"]
                or report["checkpoint"] != checkpoint
                or report["run_output_hash"] != _hash(raw)
                or report["report_hash"] != _hash(_json({
                    key: value for key, value in report.items() if key != "report_hash"
                }))):
            raise ValueError
        artifact = db.execute(
            "SELECT * FROM artifacts WHERE assignment_id=?", (assignment["assignment_id"],),
        ).fetchone()
        if artifact is None or any(artifact[key] != report[key] for key in (
            "artifact_id", "manifest_hash", "tree_hash", "commit_id",
        )):
            raise ValueError
        evidence = db.execute(
            "SELECT reference_json,payload_hash FROM evidence WHERE assignment_id=? AND evidence_type='run'",
            (assignment["assignment_id"],),
        ).fetchall()
        for item in evidence:
            reference = strict_loads(item["reference_json"])
            if not isinstance(reference, dict):
                raise ValueError
            if (item["payload_hash"] == _hash(raw)
                    and reference.get("run_id") == assignment["run_id"]
                    and reference.get("session_id") == assignment["session_id"]
                    and reference.get("status") == "completed" and reference.get("settled") is True):
                return checkpoint
        raise ValueError
    except (KeyError, TypeError, ValueError, RecursionError) as exc:
        raise _error("corrupt") from exc
