"""外部开发工具的显式协议契约。"""
from __future__ import annotations

from ..skills.parser import SKILL_NAME_PATTERN
from ..i18n import tr


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1, "maxLength": 4096}
SID = {"type": "string", "pattern": "^he_[0-9a-f]{32}$"}
RID = {"type": "string", "minLength": 1, "maxLength": 128}
VERIFICATION_ID = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"}
VERIFICATION_COMMAND = {"type": "array", "items": TEXT, "minItems": 1, "maxItems": 256}
#: 独立与插件技能使用同一裸名语法；限定名交给统一注册表验证归属。
SKILL_NAME = {"type": "string", "pattern": "^(?:" + SKILL_NAME_PATTERN + ":)?" + SKILL_NAME_PATTERN + "$"}
SESSION = {"session_id": SID}
IMAGE_ID = {"type": "string", "pattern": "^img_[0-9a-f]{32}$"}
IMAGE_FILE = obj({"download_url": {"type": "string", "minLength": 1, "maxLength": 16384},
                  "file_id": TEXT, "mime_type": TEXT, "file_name": TEXT}, ("download_url", "file_id"))
CHANGE = obj({"path": TEXT, "expected_version": {"type": "string", "pattern": "^(absent|[0-9a-f]{64})$"},
              "content": {"type": ["string", "null"]}}, ("path", "expected_version", "content"))
PIPELINE_ID = {"type": "string", "pattern": "^pl_[0-9a-f]{32}$"}
PIPELINE_STEP = obj({"kind": {"type": "string", "enum": ["exec", "verify", "image"]},
    "command": TEXT, "verification_id": VERIFICATION_ID, "image_id": IMAGE_ID,
    "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 3600000}}, ("kind",))


def definitions():
    """描述中的操作边界与服务器行为一致。"""
    return [
        ("harness_pipeline_start", tr('execution.catalog.harness_pipeline_start'), obj({**SESSION, "request_id": RID,
            "steps": {"type": "array", "items": PIPELINE_STEP, "minItems": 1, "maxItems": 30},
            "skills": {"type": "array", "items": SKILL_NAME, "maxItems": 20}},
            ("session_id", "request_id", "steps")), "execute", "pipeline_start"),
        ("harness_pipeline_get", tr('execution.catalog.harness_pipeline_get'), obj({**SESSION, "pipeline_id": PIPELINE_ID},
            ("session_id",)), "read", "pipeline_get"),
        ("harness_pipeline_control", tr('execution.catalog.harness_pipeline_control'), obj({**SESSION, "request_id": RID,
            "pipeline_id": PIPELINE_ID, "expected_revision": {"type": "integer", "minimum": 0},
            "action": {"enum": ["resume", "cancel"]}},
            ("session_id", "request_id", "pipeline_id", "expected_revision", "action")), "execute", "pipeline_control"),
        ("harness_request_get", tr('execution.catalog.harness_request_get'), obj({**SESSION, "tool": TEXT, "request_id": RID},
            ("session_id", "tool", "request_id")), "read", "request_get"),
        ("harness_image_request", tr('execution.catalog.harness_image_request'), obj({**SESSION, "request_id": RID, "prompt": {"type": "string", "minLength": 1, "maxLength": 12000}, "path": TEXT}, ("session_id", "request_id", "prompt", "path")), "workspace_write", "image_request"),
        ("harness_image_get", tr('execution.catalog.harness_image_get'), obj({**SESSION, "image_id": IMAGE_ID}, ("session_id",)), "read", "image_get"),
        ("harness_image_deliver", tr('execution.catalog.harness_image_deliver'), obj({**SESSION, "request_id": RID, "image_id": IMAGE_ID, "file": IMAGE_FILE}, ("session_id", "request_id", "image_id", "file")), "workspace_write", "image_deliver"),
        ("harness_context", tr('execution.catalog.harness_context'), obj({}), "read", "context"),
        ("load_workspace_dependencies", tr('execution.catalog.load_workspace_dependencies'), obj({
            **SESSION, "project_id": TEXT, "include_packages": {"type": "boolean", "default": True}}),
            "read", "workspace_dependencies"),
        ("harness_session_open", tr('execution.catalog.harness_session_open'), obj({"project_id": TEXT, "title": TEXT, "request_id": RID}, ("project_id", "request_id")), "workspace_write", "session_open"),
        ("harness_session_resume", tr('execution.catalog.harness_session_resume'), obj({**SESSION, "cursor": {"type": "integer", "minimum": 0}}, ("session_id",)), "read", "session_resume"),
        ("harness_read", tr('execution.catalog.harness_read'), obj({**SESSION, "path": TEXT, "offset": {"type": "integer", "minimum": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 2000}}, ("session_id", "path")), "read", "read"),
        ("harness_search", tr('execution.catalog.harness_search'), obj({**SESSION, "query": TEXT, "path": TEXT, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, ("session_id", "query")), "read", "search"),
        ("harness_apply_patch", tr('execution.catalog.harness_apply_patch'), obj({**SESSION, "request_id": RID, "changes": {"type": "array", "items": CHANGE, "minItems": 1, "maxItems": 100}}, ("session_id", "request_id", "changes")), "workspace_write", "apply_patch"),
        ("harness_exec", tr('execution.catalog.harness_exec'), obj({**SESSION, "request_id": RID, "command": TEXT, "output_encoding": {"type": "string", "description": tr("encoding.parameter")}, "timeout_ms": {"type": "integer", "minimum": 100, "maximum": 3600000}, "yield_ms": {"type": "integer", "minimum": 0, "maximum": 5000}}, ("session_id", "request_id", "command")), "execute", "exec"),
        ("harness_job_poll", tr('execution.catalog.harness_job_poll'), obj({**SESSION, "job_id": TEXT, "wait_ms": {"type": "integer", "minimum": 0, "maximum": 5000}, "stdout_cursor": {"type": "integer", "minimum": 0}, "stderr_cursor": {"type": "integer", "minimum": 0}}, ("session_id", "job_id")), "read", "job_poll"),
        ("harness_job_cancel", tr('execution.catalog.harness_job_cancel'), obj({**SESSION, "request_id": RID, "job_id": TEXT}, ("session_id", "request_id", "job_id")), "execute", "job_cancel"),
        ("harness_git_status", tr('execution.catalog.harness_git_status'), obj(SESSION, ("session_id",)), "read", "git_status"),
        ("harness_git_diff", tr('execution.catalog.harness_git_diff'), obj(SESSION, ("session_id",)), "read", "git_diff"),
        ("harness_checkpoint", tr('execution.catalog.harness_checkpoint'), obj({**SESSION, "request_id": RID, "expected_revision": {"type": "integer", "minimum": 0}, "summary": {"type": "string", "maxLength": 12000}, "next_steps": {"type": "array", "items": TEXT, "maxItems": 30}}, ("session_id", "request_id", "expected_revision", "summary", "next_steps")), "workspace_write", "checkpoint"),
        ("harness_verification_register", tr('execution.catalog.harness_verification_register'), obj({**SESSION, "request_id": RID, "verification_id": VERIFICATION_ID, "command": VERIFICATION_COMMAND}, ("session_id", "request_id", "verification_id", "command")), "execute", "verification_register"),
        ("harness_verify", tr('execution.catalog.harness_verify'), obj({**SESSION, "request_id": RID, "verification_id": TEXT, "yield_ms": {"type": "integer", "minimum": 0, "maximum": 5000}}, ("session_id", "request_id", "verification_id")), "execute", "verify"),
        ("harness_finalize", tr('execution.catalog.harness_finalize'), obj({**SESSION, "request_id": RID, "evidence_ids": {"type": "array", "items": TEXT, "minItems": 1, "maxItems": 30}, "commit_message": TEXT}, ("session_id", "request_id", "evidence_ids")), "workspace_write", "finalize"),
        ("harness_skill_list", tr('execution.catalog.harness_skill_list'), obj(SESSION, ("session_id",)), "read", "skill_list"),
        ("harness_skill_load", tr('execution.catalog.harness_skill_load'), obj({**SESSION, "name": SKILL_NAME}, ("session_id", "name")), "read", "skill_load"),
    ]
