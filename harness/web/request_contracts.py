"""管理接口的请求字段；无正文动作不补造请求体。"""
from copy import deepcopy

from .input_schemas import UPLOAD_SCHEMA
from ..app.browser import ACTIONS


def object_schema(properties, required=(), *, strict=False):
    """仅对运行时拒绝额外字段的接口声明严格字段集。"""
    result = {'type': 'object', 'properties': properties, 'required': list(required)}
    if strict:
        result['additionalProperties'] = False
    return result


TEXT = {'type': 'string'}
NULLABLE_TEXT = {'type': ['string', 'null']}
BOOLEAN = {'type': 'boolean'}
INTEGER = {'type': 'integer'}
REVISION = {'type': 'integer', 'minimum': 1}
STRING_LIST = {'type': 'array', 'items': TEXT}
STRING_MAP = {'type': 'object', 'additionalProperties': TEXT}
MEMORY_FIELDS = {'content': TEXT, 'topic': TEXT, 'pinned': BOOLEAN, 'evidence': TEXT}
CONNECTOR_FIELDS = {'name': TEXT, 'transport': {'type': 'string', 'enum': ['stdio', 'streamable-http']},
    'command': NULLABLE_TEXT, 'args': STRING_LIST, 'env': STRING_MAP, 'cwd': NULLABLE_TEXT,
    'url': NULLABLE_TEXT, 'headers': STRING_MAP, 'enabled': BOOLEAN, 'bound': BOOLEAN,
    'metadata': {'type': 'object'}}
PLUGIN_SCOPE = {'session_id': TEXT, 'scope': {'type': 'string', 'enum': ['user', 'project']}}

REQUEST_SCHEMAS = {
    ('POST', '/api/v1/attachments'): UPLOAD_SCHEMA,
    ('PATCH', '/api/v1/sessions/{session_id}'): {'$ref': '#/components/schemas/ConversationPatch'},
    ('POST', '/api/v1/workspaces'): object_schema({'path': TEXT, 'name': NULLABLE_TEXT, 'color': NULLABLE_TEXT}, ['path']),
    ('PATCH', '/api/v1/workspaces/{workspace_id}'): object_schema({'name': NULLABLE_TEXT, 'color': NULLABLE_TEXT}),
    ('POST', '/api/v1/workspaces/mkdir'): object_schema({'parent': TEXT, 'name': TEXT}, ['name']),
    ('POST', '/api/v1/sessions/{session_id}/goal'): object_schema({
        'action': {'type': 'string', 'enum': ['create', 'edit', 'pause', 'resume', 'clear']},
        'objective': TEXT, 'success_criteria': {'type': ['array', 'null'], 'items': TEXT},
        'token_budget': {'type': ['integer', 'null'], 'minimum': 1},
        'max_goal_rounds': {'type': ['integer', 'null'], 'minimum': 1},
        'goal_id': TEXT, 'revision': REVISION, 'source_seq': {'type': ['integer', 'null']}}, ['action']),
    ('POST', '/api/v1/plan-mode'): object_schema({'session_id': TEXT, 'active': BOOLEAN}, ['session_id', 'active']),
    ('POST', '/api/v1/sessions/{session_id}/git/init'): object_schema({'expected_cwd': TEXT}, ['expected_cwd'], strict=True),
    ('POST', '/api/v1/sessions/{session_id}/git/stage'): object_schema({'paths': STRING_LIST, 'expected_revision': TEXT}, ['paths', 'expected_revision'], strict=True),
    ('POST', '/api/v1/sessions/{session_id}/git/unstage'): object_schema({'paths': STRING_LIST, 'expected_revision': TEXT}, ['paths', 'expected_revision'], strict=True),
    ('POST', '/api/v1/sessions/{session_id}/git/commit'): object_schema({'message': TEXT, 'expected_revision': TEXT}, ['message', 'expected_revision'], strict=True),
    ('POST', '/api/v1/sessions/{session_id}/stop'): object_schema({'expected_turn': {'type': ['integer', 'null']}}),
    ('POST', '/api/v1/sessions/{session_id}/archive'): object_schema({'archived': BOOLEAN}),
    ('POST', '/api/v1/approvals/{question_id}'): {'$ref': '#/components/schemas/SubmitAnswers'},
    ('PATCH', '/api/v1/runs/{run_id}'): object_schema({'text': TEXT, 'expected_version': TEXT}, ['text']),
    ('POST', '/api/v1/worktrees'): object_schema({'repo': TEXT, 'ref': TEXT}),
    ('POST', '/api/v1/file/open'): object_schema({'path': TEXT, 'root': NULLABLE_TEXT, 'action': {'type': 'string', 'enum': ['default', 'terminal', 'folder']}}, ['path']),
    ('POST', '/api/v1/screenshot'): object_schema({'hideWindow': BOOLEAN, 'windowToken': TEXT}),
    ('POST', '/api/v1/sessions/{session_id}/memory'): object_schema(MEMORY_FIELDS, ['content']),
    ('PATCH', '/api/v1/sessions/{session_id}/memory/{memory_id}'): object_schema({**MEMORY_FIELDS, 'expected_revision': REVISION}, ['content', 'expected_revision']),
    ('DELETE', '/api/v1/sessions/{session_id}/memory/{memory_id}'): object_schema({'expected_revision': REVISION}, ['expected_revision']),
    ('POST', '/api/v1/sessions/{session_id}/memory/import'): object_schema({
        'format': {'type': 'string', 'enum': ['harness-project-memory-v1', 'harness-user-memory-v1', 'harness-session-memory-v1']},
        'memories': {'type': 'array', 'maxItems': 1000, 'items': object_schema({**MEMORY_FIELDS,
            'id': TEXT, 'revision': REVISION, 'source': TEXT}, ['content'])}}, ['format', 'memories']),
    ('POST', '/api/v1/connection/reset'): object_schema({'connection_id': TEXT}, ['connection_id'], strict=True),
    ('POST', '/api/v1/connection/discover'): object_schema({'provider': TEXT, 'connection': {'$ref': '#/components/schemas/ProviderConnectionDraft'}}, ['provider', 'connection'], strict=True),
    ('POST', '/api/v1/connection/authorization'): object_schema({'provider_id': TEXT, 'connection_id': TEXT, 'draft': {'type': 'object'}}, ['provider_id', 'connection_id']),
    ('POST', '/api/v1/connection/authorization/{operation_id}/answer'): object_schema({'value': TEXT}, ['value']),
    ('POST', '/api/v1/project-board/evidence'): object_schema({'task': {'$ref': '#/components/schemas/ProjectTask'}}, ['task'], strict=True),
    ('POST', '/api/v1/project-board/generate'): object_schema({'revision': TEXT, 'key': TEXT, 'goal': TEXT, 'source_session_id': NULLABLE_TEXT}, ['revision', 'key', 'goal'], strict=True),
    ('POST', '/api/v1/project-board/tasks/{task_id}/launch'): object_schema({'revision': TEXT, 'key': TEXT, 'source_session_id': NULLABLE_TEXT}, ['revision', 'key'], strict=True),
    ('POST', '/api/v1/sessions/{session_id}/team-runs/{run_id}/integration'): object_schema({'expected_seq': INTEGER}, ['expected_seq'], strict=True),
    ('POST', '/api/v1/sessions/{session_id}/worktrees/{worktree_id}/environment'): object_schema({'expected_fingerprint': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}, ['expected_fingerprint'], strict=True),
    ('POST', '/api/v1/sessions/{session_id}/turns/{turn}/feedback'): object_schema({'rating': {'enum': ['positive', 'negative', None]}, 'end_seq': INTEGER}, ['rating', 'end_seq']),
    ('POST', '/api/v1/plugins/agent-mount'): {'$ref': '#/components/schemas/SetAgentSelection'},
    ('POST', '/api/v1/plugins/skills/install'): object_schema({**PLUGIN_SCOPE, 'path': TEXT}, ['path']),
    ('POST', '/api/v1/plugins/agents/install'): object_schema({**PLUGIN_SCOPE, 'path': TEXT}, ['path']),
    ('POST', '/api/v1/plugins/agents/preview'): object_schema({'markdown': {'type': 'string', 'maxLength': 256000}}, ['markdown']),
    ('POST', '/api/v1/plugins/skills/{name}/enabled'): object_schema({**PLUGIN_SCOPE, 'enabled': BOOLEAN}, ['enabled']),
    ('POST', '/api/v1/plugins/agents/{name}/enabled'): object_schema({**PLUGIN_SCOPE, 'enabled': BOOLEAN}, ['enabled']),
    ('POST', '/api/v1/plugins/connectors/{name}/enabled'): object_schema({'enabled': BOOLEAN}, ['enabled']),
    ('POST', '/api/v1/plugins/connectors'): object_schema(CONNECTOR_FIELDS, ['name']),
    ('PATCH', '/api/v1/plugins/connectors/{name}'): object_schema(CONNECTOR_FIELDS),
    ('PATCH', '/api/v1/codex-gateway'): object_schema({'enabled': BOOLEAN}, ['enabled'], strict=True),
    ('PATCH', '/api/v1/codex-gateway/desktop'): object_schema({'enabled': BOOLEAN}, ['enabled'], strict=True),
    ('PATCH', '/api/v1/codex-gateway/accounts/{account_id}'): object_schema({'enabled': BOOLEAN}, ['enabled'], strict=True),
    ('PATCH', '/api/v1/codex-gateway/selection'): object_schema({'current_account': TEXT, 'auto_rotate': BOOLEAN}, ['current_account', 'auto_rotate'], strict=True),
    ('POST', '/api/v1/sessions/{session_id}/browser'): object_schema({
        'action': {'type': 'string', 'enum': list(ACTIONS), 'default': 'observe'},
        **{key: TEXT for key in ('url', 'screenshot_id', 'selector', 'ref', 'text', 'key', 'tab_id', 'query', 'dialog_id', 'prompt_text', 'page_id')},
        **{key: BOOLEAN for key in ('full_page', 'checked', 'accept')},
        **{key: INTEGER for key in ('width', 'height', 'duration_ms', 'offset', 'limit', 'control_revision')},
        **{key: {'type': 'number'} for key in ('x', 'y', 'delta_x', 'delta_y')}, 'values': STRING_LIST}),
    ('POST', '/api/v1/sessions/{session_id}/browser/control'): object_schema({'owner': {'enum': ['user', 'agent']}, 'expected_revision': INTEGER}, ['owner', 'expected_revision']),
    ('DELETE', '/api/v1/sessions/{session_id}/browser'): object_schema({'control_revision': INTEGER}, ['control_revision']),
    ('DELETE', '/api/v1/sessions/{session_id}/browser/diagnostics'): object_schema({'tab_id': TEXT, 'expected_revision': INTEGER}, ['tab_id', 'expected_revision']),
    ('POST', '/api/v1/voice/preparations'): object_schema({}, strict=True),
}


def request_body(method, path):
    """返回独立快照，避免一次生成修改后污染其他语言或接口。"""
    schema = REQUEST_SCHEMAS.get((method, path))
    if schema is None:
        return None
    optional = not schema.get('required') and '$ref' not in schema
    return {'required': not optional, 'content': {'application/json': {'schema': deepcopy(schema)}}}
