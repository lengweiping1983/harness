"""宿主审批操作的结构化说明；界面与问题校验共用同一契约。"""
from copy import deepcopy

from ..tools.schema import validate

APPROVAL_REASONS = (
    'tool_requires_approval', 'dangerous_tool', 'external_write', 'command_execution',
    'connector_operation', 'browser_interaction', 'network_access', 'unknown_effect', 'outside_working_directory', 'read_only_policy', 'workspace_write',
)
REVIEW_OUTCOMES = ('confirmation_requested', 'input_too_large', 'budget_unavailable', 'unavailable')
APPROVAL_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['policy', 'reasons'], 'properties': {
    'policy': {'type': 'string', 'enum': ['request_approval', 'auto_review', 'read_only']},
    'reasons': {'type': 'array', 'minItems': 1, 'maxItems': len(APPROVAL_REASONS),
                'items': {'type': 'string', 'enum': list(APPROVAL_REASONS)}},
    'review': {'type': 'string', 'enum': list(REVIEW_OUTCOMES)},
}}
OPERATION_SCHEMA = {'type': 'object', 'required': ['tool', 'cwd', 'effect'], 'additionalProperties': False, 'properties': {
    'tool': {'type': 'string', 'maxLength': 4096}, 'cwd': {'type': 'string', 'maxLength': 4096},
    'effect': {'type': 'string', 'enum': ['unknown', 'read', 'workspace_write', 'external_write', 'execute']},
    'approval': APPROVAL_SCHEMA,
}}


def normalized_operation(value):
    """拒绝矛盾说明并复制嵌套数据，不改写原请求。"""
    if validate(value, OPERATION_SCHEMA):
        return None
    approval = value.get('approval')
    if approval is not None and (len(set(approval['reasons'])) != len(approval['reasons'])
            or (approval['policy'] == 'auto_review') != ('review' in approval)):
        return None
    return deepcopy(value)
