"""当前任务合同的共享字段；验收条件逐条表达，交付策略与任务生命周期分离。"""
from __future__ import annotations

STATES = ('BACKLOG', 'READY', 'IN_PROGRESS', 'REVIEW', 'BLOCKED', 'DONE')
TRANSITIONS = {
    'BACKLOG': {'READY'}, 'READY': {'BACKLOG', 'IN_PROGRESS'},
    'IN_PROGRESS': {'REVIEW', 'BLOCKED'}, 'REVIEW': {'IN_PROGRESS', 'BLOCKED', 'DONE'},
    'BLOCKED': {'IN_PROGRESS', 'REVIEW', 'READY'}, 'DONE': set(),
}
POLICIES = ('git', 'artifact')
CONTRACT_KEYS = ('id', 'title', 'allowed_paths', 'acceptance', 'depends_on', 'verification_policy', 'artifacts')
TEXT = {'type': 'string', 'maxLength': 10000}
FIELDS = {key: dict(TEXT) for key in (
    'title', 'owner', 'branch', 'base_commit', 'blocker', 'resume_condition', 'commit',
    'target_branch', 'integration_commit', 'verification', 'execution_session_id',
    'execution_run_id', 'execution_key', 'review_error', 'review_session_id', 'review_run_id', 'review_key')}
FIELDS.update({
    'id': {'type': 'string', 'pattern': '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'},
    'status': {'type': 'string', 'enum': list(STATES)},
    'verification_policy': {'type': 'string', 'enum': list(POLICIES)},
    'acceptance': {'type': 'array', 'minItems': 1, 'maxItems': 32, 'uniqueItems': True,
                   'items': {'type': 'string', 'minLength': 1, 'maxLength': 200}},
    'allowed_paths': {'type': 'array', 'maxItems': 1000, 'uniqueItems': True,
                      'items': {'type': 'string', 'minLength': 1, 'maxLength': 1000}},
    'artifacts': {'type': 'array', 'maxItems': 16, 'uniqueItems': True,
                  'items': {'type': 'string', 'minLength': 1, 'maxLength': 1000}},
    'depends_on': {'type': 'array', 'maxItems': 1000, 'uniqueItems': True,
                   'items': {'type': 'string', 'minLength': 1, 'maxLength': 64}},
    'auto_ready': {'type': 'boolean'}, 'execution_prepared': {'type': 'boolean'},
    'verification_result': {'type': 'object', 'additionalProperties': True},
})
TASK_SCHEMA = {'type': 'object', 'required': ['id', 'title', 'status', 'allowed_paths', 'acceptance', 'depends_on', 'verification_policy'], 'properties': FIELDS}
