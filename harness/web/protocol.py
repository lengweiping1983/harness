"""版本化运行协议的公共定义；生成文档与 TypeScript，不依赖前端。"""
from __future__ import annotations

from harness.llm.context_policy import DEFAULT_WORKING_BUDGET

from harness.i18n import tr

import re
from copy import deepcopy
import json
from typing import Any
from ..app.hooks import EVENTS as HOOK_EVENTS
from ..agents.personality import STYLE_AXES, STYLE_CODES
from ..project.contracts import TASK_SCHEMA, STATES, TRANSITIONS
from ..interaction.permission_operation import APPROVAL_SCHEMA, OPERATION_SCHEMA
from .query import QUERY_SCHEMAS, query_parameters
from .input_schemas import SESSION_PATCH, TITLE
from .request_contracts import request_body
from ..academy.protocol import SCHEMAS as ACADEMY_SCHEMAS, ENDPOINTS as ACADEMY_ENDPOINTS
from .superagent_protocol import SCHEMAS as SUPERAGENT_SCHEMAS, ENDPOINTS as SUPERAGENT_ENDPOINTS

PERSONALITY_CODE = {'anyOf': [{'type': 'string', 'enum': list(STYLE_CODES)}, {'type': 'null'}]}

SCHEMAS: dict[str, dict[str, Any]] = {
    'ModelCredentialStatus': {'type': 'object', 'required': ['has_model_credentials'], 'properties': {
        'has_model_credentials': {'type': 'boolean'}}},
    'WorkspaceRecord': {'type': 'object', 'required': ['id', 'path', 'name', 'color'], 'properties': {
        'id': {'type': 'string', 'minLength': 1}, 'path': {'type': 'string', 'minLength': 1},
        'name': {'type': 'string'}, 'color': {'type': 'string'}, 'exists': {'type': 'boolean'},
        'created_at': {'type': 'number'}, 'last_used_at': {'type': 'number'}}},
    'WorkspaceCatalog': {'type': 'object', 'required': ['workspaces', 'default'], 'properties': {
        'workspaces': {'type': 'array', 'items': {'$ref': '#/components/schemas/WorkspaceRecord'}},
        'default': {'type': 'string'}}},
    'VoicePreparation': {'type': 'object', 'required': ['ready'], 'additionalProperties': False,
                         'properties': {'ready': {'const': True}}},
    'VoiceTranscript': {'type': 'object', 'required': ['transcript'], 'additionalProperties': False,
                        'properties': {'transcript': {'type': 'string'}}},
    'VoiceDictationCapabilities': {'type': 'object', 'additionalProperties': False,
        'required': ['available', 'cpu_supported', 'platform', 'download_url', 'reason'], 'properties': {
            'available': {'type': 'boolean'}, 'cpu_supported': {'type': 'boolean'},
            'platform': {'type': 'string', 'enum': ['macos', 'windows', 'unsupported']},
            'download_url': {'type': ['string', 'null']},
            'reason': {'anyOf': [{'type': 'null'}, {'$ref': '#/components/schemas/ResourceCopy'}]}}},
    'VoiceDictationOpen': {'type': 'object', 'additionalProperties': False, 'required': ['requested'],
        'properties': {'requested': {'const': True}}},
    'ConversationProject': {'type': 'object', 'additionalProperties': False, 'required': ['workspace_id', 'mode'], 'properties': {
        'workspace_id': {'type': 'string', 'minLength': 1}, 'mode': {'type': 'string', 'enum': ['local', 'worktree']},
        'ref': {'type': 'string', 'minLength': 1}, 'expected_head': {'type': ['string', 'null']},
        'expected_branch': {'type': ['string', 'null']}}},
    'NewConversationRequest': {'type': 'object', 'additionalProperties': False, 'properties': {
        'cwd': {'type': ['string', 'null']}, 'title': TITLE, 'model': {'type': ['string', 'null']},
        'agent_selection': {'type': 'string', 'minLength': 1}, 'project': {'$ref': '#/components/schemas/ConversationProject'}}},
    'ConversationPatch': SESSION_PATCH,
    'WorkspaceOptions': {'type': 'object', 'required': ['cwd', 'is_git', 'root', 'branch', 'head', 'refs', 'local_branches', 'dirty'], 'properties': {
        'cwd': {'type': 'string'}, 'is_git': {'type': ['boolean', 'null']}, 'root': {'type': ['string', 'null']},
        'branch': {'type': ['string', 'null']}, 'head': {'type': ['string', 'null']}, 'dirty': {'type': ['boolean', 'null']},
        'refs': {'type': 'array', 'items': {'type': 'string'}}, 'local_branches': {'type': 'array', 'items': {'type': 'string'}}}},
    'ResourceCopy': {'type': 'object', 'required': ['key'], 'properties': {
        'key': {'type': 'string'}, 'params': {'type': 'object', 'additionalProperties': {'anyOf': [{'type': 'string'}, {'type': 'number'}]}}}},
    'RunFailureDetails': {'type': 'object', 'properties': {'copy': {'$ref': '#/components/schemas/ResourceCopy'}}},
    'RunFailure': {'type': 'object', 'properties': {
        'code': {'type': 'string'}, 'message': {'type': 'string'}, 'error': {'type': 'string'},
        'copy': {'$ref': '#/components/schemas/ResourceCopy'}, 'details': {'$ref': '#/components/schemas/RunFailureDetails'}}},
    'AgentErrorPart': {'type': 'object', 'required': ['label', 'message'], 'properties': {
        'label': {'type': 'string'}, 'message': {'anyOf': [{'type': 'string'}, {'$ref': '#/components/schemas/ResourceCopy'}]}}},
    'McpServiceWorkspace': {'type': 'object', 'required': ['id', 'name', 'path', 'project_id'], 'properties': {
        'id': {'type': 'string'}, 'name': {'type': 'string'}, 'path': {'type': 'string'}, 'project_id': {'type': 'string'}}},
    'McpTunnelSettings': {'type': 'object', 'required': ['mode', 'public_url', 'origin_port'], 'additionalProperties': False, 'properties': {
        'mode': {'type': 'string', 'enum': ['quick', 'named']}, 'public_url': {'type': 'string', 'maxLength': 2048},
        'origin_port': {'type': 'integer', 'minimum': 1024, 'maximum': 65535}, 'token': {'type': 'string', 'maxLength': 8192, 'writeOnly': True}}},
    'McpTunnelSummary': {'type': 'object', 'required': ['mode', 'public_url', 'origin_port', 'has_token'], 'properties': {
        'mode': {'type': 'string', 'enum': ['quick', 'named']}, 'public_url': {'type': 'string'},
        'origin_port': {'type': 'integer'}, 'has_token': {'type': 'boolean'}, 'token_mask': {'type': 'string'}}},
    'ChatCompanionSettings': {'type': 'object', 'additionalProperties': False, 'required': ['enabled', 'port'], 'properties': {
        'enabled': {'type': 'boolean'}, 'port': {'type': 'integer', 'minimum': 1024, 'maximum': 65535}}},
    'ChatCompanionState': {'type': 'object', 'required': ['enabled', 'port', 'status', 'endpoint', 'executor', 'token_mask', 'external', 'error', 'error_copy', 'extension_path', 'extension_version'], 'properties': {
        'enabled': {'type': 'boolean'}, 'port': {'type': 'integer'}, 'status': {'type': 'string', 'enum': ['disabled', 'running', 'failed', 'stopped']},
        'endpoint': {'type': 'string'}, 'executor': {'type': 'string'}, 'token_mask': {'type': 'string'}, 'external': {'type': 'boolean'},
        'error': {'type': 'string'}, 'error_copy': {'anyOf': [{'$ref': '#/components/schemas/ResourceCopy'}, {'type': 'null'}]},
        'extension_path': {'type': 'string'}, 'extension_version': {'type': 'string'}}},
    'McpServiceStart': {'type': 'object', 'additionalProperties': False, 'properties': {
        'scope': {'type': 'string', 'enum': ['all', 'selected']}, 'workspace_id': {'type': 'string', 'minLength': 1}, 'tunnel': {'$ref': '#/components/schemas/McpTunnelSettings'}}},
    'McpServiceState': {'type': 'object', 'required': ['status', 'local_url', 'public_url', 'project_root', 'error', 'error_copy'], 'properties': {
        'status': {'type': 'string', 'enum': ['stopped', 'starting', 'running', 'stopping', 'failed']},
        'password_mask': {'type': 'string'},
        'companion': {'$ref': '#/components/schemas/ChatCompanionState'},
        'tunnel': {'$ref': '#/components/schemas/McpTunnelSummary'}, 'quick_retry_at': {'type': 'integer'}, 'reconnecting': {'type': 'boolean'}, 'tunnel_diagnostic': {'type': 'string'},
        'local_url': {'type': 'string'}, 'public_url': {'type': 'string'}, 'project_root': {'type': 'string'},
        'error_details': {'type': 'array', 'items': {'anyOf': [{'type': 'string'}, {'$ref': '#/components/schemas/ResourceCopy'}]}},
        'error': {'type': 'string'}, 'error_copy': {'anyOf': [{'$ref': '#/components/schemas/ResourceCopy'}, {'type': 'null'}]},
        'project_id': {'type': 'string'}, 'project_scope': {'type': 'string', 'enum': ['all', 'selected']},
        'default_workspace_id': {'type': 'string'},
        'workspaces': {'type': 'array', 'items': {'$ref': '#/components/schemas/McpServiceWorkspace'}}}},
    'MemoryReference': {'type': 'object', 'required': ['id', 'revision', 'reason', 'source_status', 'derivation_status'], 'properties': {
        'id': {'type': 'string'}, 'revision': {'type': 'integer', 'minimum': 1},
        'reason': {'type': 'string', 'enum': ['pinned', 'query']},
        'source_status': {'type': 'string', 'enum': ['untracked', 'unchanged', 'changed', 'missing',
            'unavailable', 'unsafe', 'unchecked', 'invalid']},
        'derivation_status': {'type': 'string', 'enum': ['none', 'current', 'changed', 'missing', 'invalid', 'unchecked']}}},
    'MemorySuppression': {'type': 'object', 'required': ['id', 'revision', 'summary_id', 'summary_revision'],
        'properties': {'id': {'type': 'string'}, 'revision': {'type': 'integer', 'minimum': 1},
        'summary_id': {'type': 'string'}, 'summary_revision': {'type': 'integer', 'minimum': 1}}},
    'MemoryRecall': {'type': 'object', 'required': ['budget', 'used_chars', 'total', 'selected',
        'skipped_budget', 'skipped_query_limit', 'unmatched', 'suppressed'], 'properties': {
        'budget': {'type': 'integer', 'minimum': 0}, 'used_chars': {'type': 'integer', 'minimum': 0},
        'total': {'type': 'integer', 'minimum': 0},
        'selected': {'type': 'array', 'items': {'$ref': '#/components/schemas/MemoryReference'}},
        'skipped_budget': {'type': 'array', 'items': {'$ref': '#/components/schemas/MemoryReference'}},
        'suppressed': {'type': 'array', 'items': {'$ref': '#/components/schemas/MemorySuppression'}},
        'skipped_query_limit': {'type': 'integer', 'minimum': 0}, 'unmatched': {'type': 'integer', 'minimum': 0}}},
    'MemoryLayerPreview': {'type': 'object', 'required': ['scope', 'status', 'budget', 'recall', 'error_code'],
        'properties': {'scope': {'type': 'string', 'enum': ['user', 'project', 'session']},
        'status': {'type': 'string', 'enum': ['ready', 'unavailable', 'unbound']},
        'budget': {'type': 'integer', 'minimum': 0},
        'recall': {'anyOf': [{'$ref': '#/components/schemas/MemoryRecall'}, {'type': 'null'}]},
        'error_code': {'type': 'string'}}},
    'MemoryPreview': {'type': 'object', 'required': ['session_id', 'source_seq', 'budget', 'used_chars', 'layers'],
        'properties': {'session_id': {'type': 'string'}, 'source_seq': {'type': ['integer', 'null']},
        'budget': {'type': 'integer', 'minimum': 0}, 'used_chars': {'type': 'integer', 'minimum': 0},
        'layers': {'type': 'array', 'items': {'$ref': '#/components/schemas/MemoryLayerPreview'}}}},
    'AgentSkillSelection': {'type': ['array', 'null'], 'maxItems': 1000, 'uniqueItems': True,
        'items': {'type': 'string', 'maxLength': 200}},
    'AgentMemberDefinition': {'type': 'object', 'required': ['name'], 'properties': {
        'name': {'type': 'string'}, 'role': {'type': 'string', 'enum': ['lead', 'member']},
        'agent': {'type': ['string', 'null']}, 'prompt': {'type': 'string'}, 'model': {'type': ['string', 'null']},
        'display_name': {'type': ['string', 'null']}, 'execution': {'type': 'string', 'enum': ['inherit', 'model']},
        'runner': {'anyOf': [{'type': 'string', 'enum': ['codex', 'claude', 'opencode']}, {'type': 'null'}]},
        'access': {'type': 'string', 'enum': ['inherit', 'read']},
        'depends_on': {'type': 'array', 'items': {'type': 'string'}},
        'skills': {'$ref': '#/components/schemas/AgentSkillSelection'}}},
    'AgentDefinitionInput': {'type': 'object', 'required': ['name'], 'properties': {
        'name': {'type': 'string'}, 'description': {'type': 'string'}, 'system_prompt': {'type': 'string'},
        'agent_type': {'type': 'string', 'enum': ['agent', 'team']}, 'model': {'type': ['string', 'null']},
        'runner': {'anyOf': [{'type': 'string', 'enum': ['', 'codex', 'claude', 'opencode']}, {'type': 'null'}]},
        'metadata': {'type': 'object'},
        'members': {'type': 'array', 'items': {'$ref': '#/components/schemas/AgentMemberDefinition'}},
        'skills': {'$ref': '#/components/schemas/AgentSkillSelection'},
        'scope': {'type': 'string', 'enum': ['user', 'project']}, 'session_id': {'type': ['string', 'null']}}},
    'AgentSavedIdentity': {'type': 'object', 'required': ['name', 'agent_type'], 'properties': {
        'name': {'type': 'string', 'minLength': 1}, 'agent_type': {'type': 'string', 'enum': ['agent', 'team']}}},
    'AgentRefreshError': {'type': 'object', 'required': ['session_id', 'code', 'message'], 'properties': {
        'session_id': {'type': 'string'}, 'code': {'type': 'string'}, 'message': {'type': 'string'}}},
    'AgentCreatedResponse': {'type': 'object', 'required': ['created', 'agent'], 'properties': {
        'created': {'type': 'boolean', 'const': True}, 'agent': {'$ref': '#/components/schemas/AgentSavedIdentity'},
        'mount': {'$ref': '#/components/schemas/AgentSelectionState'},
        'refresh_errors': {'type': 'array', 'items': {'$ref': '#/components/schemas/AgentRefreshError'}}}},
    'AgentUpdatedResponse': {'type': 'object', 'required': ['updated', 'agent', 'revision'], 'properties': {
        'updated': {'type': 'boolean', 'const': True}, 'agent': {'$ref': '#/components/schemas/AgentSavedIdentity'},
        'revision': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
        'mount': {'$ref': '#/components/schemas/AgentSelectionState'},
        'refresh_errors': {'type': 'array', 'items': {'$ref': '#/components/schemas/AgentRefreshError'}}}},
    'ProjectTask': TASK_SCHEMA,
    'ProjectBoard': {'type': 'object', 'required': ['schema', 'tasks', 'revision', 'path', 'file_exists'], 'properties': {
        'schema': {'type': 'integer', 'const': 2}, 'tasks': {'type': 'array', 'items': {'$ref': '#/components/schemas/ProjectTask'}},
        'revision': {'type': 'string'}, 'path': {'type': 'string'}, 'file_exists': {'type': 'boolean'}}},
    'AgentTeamRecipient': {'type': 'object', 'required': ['name', 'display_name', 'role', 'identity'], 'properties': {
        'name': {'type': 'string'}, 'display_name': {'type': 'string'}, 'identity': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
        'role': {'type': 'string', 'enum': ['lead', 'member']}}},
    'AgentSelectionState': {'type': 'object', 'required': ['available', 'agent', 'revision'], 'properties': {
        'available': {'type': 'boolean'}, 'agent': {'type': ['string', 'null']}, 'revision': {'type': 'integer'},
        'agent_type': {'type': 'string', 'enum': ['agent', 'team']},
        'team_worktrees': {'type': 'boolean'}, 'team_working_copy': {'type': 'boolean'},
        'selection_unavailable': {'type': 'boolean'}, 'description': {'type': 'string'},
        'display_name': {'type': 'string'}, 'profession': {'type': 'string'},
        'runner': {'type': 'string', 'enum': ['codex', 'claude', 'opencode']},
        'model': {'type': ['string', 'null']},
        'members': {'type': 'array', 'items': {'$ref': '#/components/schemas/AgentTeamRecipient'}}}},
    'SetAgentSelection': {'type': 'object', 'required': ['session_id', 'name'], 'properties': {
        'session_id': {'type': 'string'}, 'name': {'type': ['string', 'null']},
        'team_worktrees': {'type': 'boolean'}, 'team_working_copy': {'type': 'boolean'}}},
    'AgentSelectionResponse': {'type': 'object', 'required': ['mount'], 'properties': {
        'mount': {'$ref': '#/components/schemas/AgentSelectionState'}}},
    'PersonalityProfile': {'type': 'object', 'required': ['id', 'source', 'code', 'name', 'description', 'prompt'], 'properties': {
        'id': {'type': 'string'}, 'source': {'type': 'string', 'enum': ['builtin', 'user']},
        'code': PERSONALITY_CODE,
        'name': {'type': 'string', 'maxLength': 120}, 'description': {'type': 'string', 'maxLength': 500},
        'prompt': {'type': 'string', 'maxLength': 1000}, 'revision': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}},
    'PersonalitySelection': {'type': 'object', 'required': ['profile', 'revision'], 'properties': {
        'profile': {'anyOf': [{'$ref': '#/components/schemas/PersonalityProfile'}, {'type': 'null'}]},
        'revision': {'type': 'string'}}},
    'SetPersonalitySelection': {'type': 'object', 'required': ['profile_id', 'expected_revision'], 'properties': {
        'profile_id': {'type': ['string', 'null']}, 'expected_revision': {'type': 'string'}}},
    'PersonalityCatalog': {'type': 'object', 'required': ['profiles'], 'properties': {
        'profiles': {'type': 'array', 'items': {'$ref': '#/components/schemas/PersonalityProfile'}},
        'warning': {'type': 'string'}}},
    'SavePersonalityProfile': {'type': 'object', 'required': ['name', 'prompt'], 'properties': {
        'id': {'type': 'string', 'pattern': '^[a-f0-9]{32}$'}, 'revision': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
        'name': {'type': 'string', 'minLength': 1, 'maxLength': 120}, 'description': {'type': 'string', 'maxLength': 500},
        'prompt': {'type': 'string', 'minLength': 1, 'maxLength': 1000}}},
    'DeletePersonalityProfile': {'type': 'object', 'required': ['revision'], 'properties': {
        'revision': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}},
    'SessionPersonality': {'type': 'object', 'required': ['code', 'effective', 'source', 'inherited', 'prompt', 'default_prompt', 'version'], 'properties': {
        'code': PERSONALITY_CODE, 'effective': PERSONALITY_CODE,
        'source': {'type': 'string', 'enum': ['session', 'parent', 'agent', 'default']}, 'inherited': {'type': 'boolean'},
        'prompt': {'type': ['string', 'null'], 'maxLength': 1000}, 'default_prompt': {'type': ['string', 'null'], 'maxLength': 1000},
        'version': {'type': 'integer', 'minimum': -1}}},
    'SetSessionPersonality': {'type': 'object', 'required': ['code', 'expected_version'], 'properties': {
        'code': PERSONALITY_CODE,
        'prompt': {'type': ['string', 'null'], 'maxLength': 1000}, 'expected_version': {'type': 'integer', 'minimum': -1}}},
    'RecommendPersonality': {'type': 'object', 'required': ['choices'], 'properties': {
        'choices': {'type': 'array', 'minItems': 4, 'maxItems': 4, 'items': {'type': 'string', 'enum': [choice for axis in STYLE_AXES for choice in axis]},
                    'prefixItems': [{'type': 'string', 'enum': list(axis)} for axis in STYLE_AXES]}}},
    'ChatGPTModel': {'type': 'object', 'required': ['id', 'label', 'provider', 'description', 'supports_reasoning', 'reasoning_efforts'], 'properties': {
        'id': {'type': 'string'}, 'label': {'type': 'string'}, 'provider': {'type': 'string', 'const': 'chatgpt'},
        'description': {'type': 'string'}, 'supports_reasoning': {'type': 'boolean'},
        'reasoning_efforts': {'type': 'array', 'items': {'type': 'string'}},
        'context_window': {'type': 'integer'}, 'max_output': {'type': 'integer'}}},
    'ChatGPTAccountStatus': {'type': 'object', 'required': ['logged_in', 'email', 'pending', 'error', 'models', 'selected'], 'properties': {
        'logged_in': {'type': 'boolean'}, 'email': {'type': 'string'}, 'pending': {'type': 'boolean'},
        'error': {'type': 'string'}, 'selected': {'type': 'string'}, 'authorization_url': {'type': 'string'},
        'models': {'type': 'array', 'items': {'$ref': '#/components/schemas/ChatGPTModel'}}}},
    'ConnectionKeyStatus': {'type': 'object', 'required': ['id', 'mask', 'available', 'current', 'in_use'], 'properties': {
        'id': {'type': 'string'}, 'mask': {'type': 'string'}, 'available': {'type': 'boolean'}, 'current': {'type': 'boolean'}, 'in_use': {'type': 'boolean'}}},
    'ConnectionModel': {'type': 'object', 'required': ['id', 'model', 'base_url', 'enabled', 'keys'], 'properties': {
        'id': {'type': 'string'}, 'model': {'type': 'string'}, 'base_url': {'type': 'string'}, 'enabled': {'type': 'boolean'},
        'context_window': {'type': ['integer', 'null'], 'minimum': 1},
        'max_output_tokens': {'type': ['integer', 'null'], 'minimum': 1},
        'capabilities': {'type': 'object'},
        'keys': {'type': 'array', 'items': {'$ref': '#/components/schemas/ConnectionKeyStatus'}}}},
    'ConnectionCurrent': {'type': 'object', 'required': ['model_id', 'model', 'key_id'], 'properties': {
        'model_id': {'type': 'string'}, 'model': {'type': 'string'}, 'key_id': {'type': 'string'}}},
    'ConnectionRetiringKey': {'type': 'object', 'required': ['model_id', 'model', 'key_id', 'mask'], 'properties': {
        'model_id': {'type': 'string'}, 'model': {'type': 'string'}, 'key_id': {'type': 'string'}, 'mask': {'type': 'string'}}},
    'ConnectionRouting': {'type': 'object', 'required': ['models', 'cycle'], 'properties': {
        'models': {'type': 'array', 'items': {'$ref': '#/components/schemas/ConnectionModel'}},
        'current': {'anyOf': [{'$ref': '#/components/schemas/ConnectionCurrent'}, {'type': 'null'}]}, 'cycle': {'type': 'integer'},
        'retry_in_seconds': {'type': 'number'}, 'state_save_failed': {'type': 'boolean'},
        'retiring': {'type': 'array', 'items': {'$ref': '#/components/schemas/ConnectionRetiringKey'}}}},
    'ConnectionKeyInput': {'type': 'object', 'properties': {'id': {'type': 'string'}, 'value': {'type': 'string', 'writeOnly': True}}},
    'ConnectionModelInput': {'type': 'object', 'required': ['model', 'base_url', 'keys'], 'properties': {
        'id': {'type': 'string'}, 'model': {'type': 'string'}, 'base_url': {'type': 'string'}, 'enabled': {'type': 'boolean'},
        'context_window': {'type': ['integer', 'null'], 'minimum': 1},
        'max_output_tokens': {'type': ['integer', 'null'], 'minimum': 1},
        'keys': {'type': 'array', 'minItems': 1, 'items': {'$ref': '#/components/schemas/ConnectionKeyInput'}}}},
    'SetConnection': {'type': 'object', 'required': ['models'], 'additionalProperties': False, 'properties': {
        'models': {'type': 'array', 'minItems': 1, 'items': {'$ref': '#/components/schemas/ConnectionModelInput'}}}},
    'SetConnectionKeyStatus': {'type': 'object', 'required': ['model_id', 'key_id', 'available'], 'properties': {
        'model_id': {'type': 'string'}, 'key_id': {'type': 'string'}, 'available': {'type': 'boolean'}}},
    'PermissionGrant': {'type': 'object', 'required': ['id', 'tool', 'cwd', 'call_id', 'remaining_seconds'], 'properties': {
        'id': {'type': 'string'}, 'tool': {'type': 'string'}, 'cwd': {'type': 'string'}, 'call_id': {'type': 'string'},
        'remaining_seconds': {'type': 'integer', 'minimum': 0}, 'operation': {'type': 'string', 'maxLength': 64000},
    }},
    'PermissionGrants': {'type': 'object', 'required': ['scope', 'parent_controlled', 'grants'], 'properties': {
        'scope': {'type': 'string'}, 'parent_controlled': {'type': 'boolean'}, 'grants': {'type': 'array', 'items': {'$ref': '#/components/schemas/PermissionGrant'}},
    }},
    'PlanEvidencePage': {'type': 'object', 'required': ['call_id', 'name', 'source_seq', 'offset', 'text', 'total_chars', 'next_offset'], 'properties': {
        'call_id': {'type': 'string'}, 'name': {'type': 'string'}, 'source_seq': {'type': 'integer'},
        'offset': {'type': 'integer', 'minimum': 0}, 'text': {'type': 'string', 'maxLength': 20000},
        'total_chars': {'type': 'integer', 'minimum': 0}, 'next_offset': {'type': ['integer', 'null']},
    }},
    'TodoEvidence': {'type': 'object', 'required': ['call_id', 'name', 'source_seq', 'summary', 'truncated'], 'properties': {
        'call_id': {'type': 'string'}, 'name': {'type': 'string'}, 'source_seq': {'type': 'integer'},
        'summary': {'type': 'string', 'maxLength': 2000}, 'truncated': {'type': 'boolean'},
    }},
    'ImplementationTodo': {'type': 'object', 'required': ['content', 'status'], 'properties': {
        'id': {'type': 'string'},
        'content': {'type': 'string'}, 'status': {'type': 'string', 'enum': ['pending', 'in_progress', 'completed']}, 'plan_id': {'type': 'string'},
        'evidence': {'type': 'array', 'items': {'$ref': '#/components/schemas/TodoEvidence'}},
    }},
    'SubagentDetail': {'type': 'object', 'required': ['agent_id', 'description', 'status', 'parent_session_id', 'created_at', 'duration_ms', 'result', 'error', 'tool_calls', 'pending_messages', 'prompt'], 'properties': {
        'agent_id': {'type': 'string'}, 'description': {'type': 'string'},
        'status': {'type': 'string', 'enum': ['pending', 'running', 'completed', 'failed', 'interrupted']},
        'parent_session_id': {'type': 'string'}, 'created_at': {'type': 'integer'}, 'duration_ms': {'type': ['integer', 'null']},
        'child_session_id': {'type': ['string', 'null']}, 'parent_agent_id': {'type': ['string', 'null']},
        'root_session_id': {'type': ['string', 'null']},
        'context_persisted': {'type': 'boolean'},
        'result': {'type': 'string'}, 'error': {'type': ['string', 'null']},
        'tool_calls': {'type': 'integer'}, 'pending_messages': {'type': 'integer'}, 'prompt': {'type': 'string'},
        'archived': {'type': 'boolean'}, 'recovered': {'type': 'boolean'}, 'archive_error': {'type': 'string'},
        'error_parts': {'type': 'array', 'items': {'$ref': '#/components/schemas/AgentErrorPart'}},
        'error_copy': {'$ref': '#/components/schemas/ResourceCopy'},
        'archive_error_copy': {'$ref': '#/components/schemas/ResourceCopy'},
    }},
    'SubagentDetailResponse': {'type': 'object', 'required': ['agent'], 'properties': {
        'agent': {'$ref': '#/components/schemas/SubagentDetail'}, 'audit_error': {'type': 'string'},
    }},
    'SubagentPage': {'type': 'object', 'required': ['agents', 'has_more', 'next_before'], 'properties': {
        'agents': {'type': 'array', 'items': {'$ref': '#/components/schemas/SubagentView'}},
        'has_more': {'type': 'boolean'}, 'next_before': {'type': ['string', 'null']},
    }},
    'PlanVersion': {'type': 'object', 'required': ['id', 'title', 'status', 'feedback', 'previous_id', 'seq', 'submitted_at', 'decided_at', 'content_hash'], 'properties': {
        'id': {'type': 'string'}, 'title': {'type': 'string'},
        'content_hash': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
        'status': {'type': 'string', 'enum': ['pending', 'revision_requested', 'approved', 'closed', 'superseded']},
        'feedback': {'type': 'string'}, 'previous_id': {'type': ['string', 'null']}, 'seq': {'type': 'integer'},
        'submitted_at': {'type': 'integer'}, 'decided_at': {'type': ['integer', 'null']},
    }},
    'PlanDetail': {'type': 'object', 'required': ['id', 'title', 'status', 'feedback', 'previous_id', 'seq', 'submitted_at', 'decided_at', 'content_hash', 'plan', 'diff'], 'properties': {
        'id': {'type': 'string'}, 'title': {'type': 'string'},
        'content_hash': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
        'status': {'type': 'string', 'enum': ['pending', 'revision_requested', 'approved', 'closed', 'superseded']},
        'feedback': {'type': 'string'}, 'previous_id': {'type': ['string', 'null']}, 'seq': {'type': 'integer'},
        'submitted_at': {'type': 'integer'}, 'decided_at': {'type': ['integer', 'null']},
        'plan': {'type': 'string'}, 'diff': {'type': 'string'},
        'implementation': {'type': 'array', 'items': {'$ref': '#/components/schemas/ImplementationTodo'}},
    }},
    'SessionPermissionState': {'type': 'object', 'required': ['session_id', 'scope', 'permission_policy', 'default_policy', 'inherited', 'version'], 'properties': {
        'session_id': {'type': 'string'}, 'scope': {'type': 'string'},
        'permission_policy': {'type': 'string'}, 'default_policy': {'type': 'string'},
        'inherited': {'type': 'boolean'}, 'version': {'type': 'string', 'minLength': 64, 'maxLength': 64},
    }},
    'SessionPermissionResponse': {'type': 'object', 'required': ['permissions'], 'properties': {
        'permissions': {'$ref': '#/components/schemas/SessionPermissionState'},
    }},
    'SetSessionPermission': {'type': 'object', 'additionalProperties': False, 'required': ['permission_policy', 'expected_version'], 'properties': {
        'permission_policy': {'anyOf': [{'type': 'string', 'enum': ['request_approval', 'auto_review', 'full_access', 'read_only']}, {'type': 'null'}]},
        'expected_version': {'type': 'string', 'minLength': 64, 'maxLength': 64},
    }},
    'SteeringReceipt': {'type': 'object', 'required': ['request_id', 'turn', 'text', 'status', 'accepted_seq', 'accepted_at', 'applied_seq', 'reason', 'text_truncated', 'team_delivered', 'delivery_seq'], 'properties': {
        'request_id': {'type': 'string'}, 'turn': {'type': 'integer'}, 'text': {'type': 'string'},
        'status': {'type': 'string', 'enum': ['pending', 'applied', 'unapplied']},
        'accepted_seq': {'type': 'integer'}, 'accepted_at': {'type': 'integer'},
        'applied_seq': {'type': ['integer', 'null']}, 'reason': {'type': 'string'}, 'text_truncated': {'type': 'boolean'},
        'team_delivered': {'type': 'boolean'}, 'delivery_seq': {'type': ['integer', 'null']},
        'attachments': {'type': 'array', 'items': {'$ref': '#/components/schemas/QueuedAttachment'}},
    }},
    'SteeringStatus': {'type': 'object', 'required': ['receipt'], 'properties': {'receipt': {'anyOf': [{'$ref': '#/components/schemas/SteeringReceipt'}, {'type': 'null'}]}}},
    'SteerAccepted': {'type': 'object', 'required': ['session_id', 'turn', 'accepted', 'request_id', 'receipt'], 'properties': {
        'session_id': {'type': 'string'}, 'turn': {'type': 'integer'}, 'accepted': {'type': 'boolean'}, 'request_id': {'type': 'string'},
        'receipt': {'$ref': '#/components/schemas/SteeringReceipt'},
    }},
    "ConversationUserMessage": {"type": "object", "required": ["source_seq", "text"], "properties": {
        "source_seq": {"type": "integer", "minimum": 0}, "text": {"type": "string", "maxLength": 240},
    }},
    "ConversationHistory": {"type": "object", "required": ["through", "start", "end", "total", "has_more", "next_cursor", "user_messages"], "properties": {
        "through": {"type": "integer", "minimum": -1},
        "start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 0},
        "total": {"type": "integer", "minimum": 0},
        "has_more": {"type": "boolean"}, "next_cursor": {"type": ["string", "null"]},
        "user_messages": {"type": "array", "items": {"$ref": "#/components/schemas/ConversationUserMessage"}},
    }},
    "ConversationPage": {"type": "object", "required": ["header", "messages", "events", "events_view", "history"], "properties": {
        "header": {"type": "object"}, "messages": {"type": "array", "items": {"type": "object"}},
        "events": {"type": "array", "items": {"type": "object"}},
        "events_view": {"type": "string", "enum": ["conversation-window"]},
        "history": {"$ref": "#/components/schemas/ConversationHistory"},
    }},
    "SubmitRun": {"type": "object", "required": ["text"], "properties": {
        "text": {"type": "string", "minLength": 1},
        "recipient_identities": {"type": "object", "additionalProperties": {"type": "string"}},
        "recipient_team": {"type": "string"}, "selection_revision": {"type": "integer", "minimum": 0},
        "recipients": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "string"}},
    }},
    "TeamMemberPreview": {"type": "object", "required": ["run_id", "member", "agent_id", "request_id", "version", "stage", "status", "text", "chars_total", "truncated"], "properties": {
        "run_id": {"type": "string"}, "member": {"type": "string"}, "agent_id": {"type": "string"}, "request_id": {"type": "string"},
        "version": {"type": "integer", "minimum": 1}, "stage": {"type": "string", "enum": ["member", "reply", "summary", "planning"]},
        "status": {"type": "string", "enum": ["streaming", "cleared"]}, "text": {"type": "string", "maxLength": 12000},
        "chars_total": {"type": "integer", "minimum": 0}, "truncated": {"type": "boolean"},
        "thinking": {"type": "string", "maxLength": 12000}, "thinking_truncated": {"type": "boolean"},
    }},
    "TeamMemberPreviewRecovery": {"type": "object", "required": ["member_previews"], "properties": {
        "member_previews": {"type": "array", "maxItems": 32, "items": {"$ref": "#/components/schemas/TeamMemberPreview"}},
    }},
    "TeamMessageAuthor": {"type": "object", "required": ["team", "member", "display_name", "role", "stage", "identity"], "properties": {
        "identity": {"type": "string", "pattern": "^[a-f0-9]{64}$"}, "team": {"type": "string"}, "member": {"type": "string"}, "display_name": {"type": "string"},
        "role": {"type": "string", "enum": ["lead", "member"]},
        "stage": {"type": "string", "enum": ["member", "summary", "reply"]}, "agent": {"type": "string"},
    }},
    "TeamRoute": {"type": "object", "additionalProperties": False, "required": ["team", "revision", "recipients", "selection_revision", "recipient_identities"], "properties": {
        "team": {"type": "string"}, "revision": {"type": "string"}, "selection_revision": {"type": "integer", "minimum": 0},
        "recipient_identities": {"type": "object", "additionalProperties": {"type": "string"}},
        "recipients": {"type": "array", "items": {"type": "string"}},
    }},
    "QueuedAttachment": {"type": "object", "required": ["attachmentId", "name", "mediaType", "size", "isImage"], "properties": {
        "attachmentId": {"type": "string"}, "name": {"type": "string"}, "mediaType": {"type": "string"},
        "size": {"type": "integer"}, "isImage": {"type": "boolean"},
    }},
    "QueueEntry": {"type": "object", "required": ["id", "text", "edit_version"], "properties": {
        "requester": {"$ref": "#/components/schemas/HumanRequester"},
        "id": {"type": "string"}, "text": {"type": "string"}, "edit_version": {"type": "string", "minLength": 64, "maxLength": 64},
        "team_route": {"$ref": "#/components/schemas/TeamRoute"},
        "attachment_refs": {"type": "array", "items": {"$ref": "#/components/schemas/QueuedAttachment"}},
    }},
    "EditQueuedInput": {"type": "object", "required": ["text", "expected_version"], "properties": {
        "text": {"type": "string"}, "expected_version": {"type": "string", "minLength": 64, "maxLength": 64},
    }},
    "QueueControl": {"type": "object", "required": ["session_id", "paused", "pause_reason", "updated_at", "pending", "running"], "properties": {
        "session_id": {"type": "string"}, "paused": {"type": "boolean"},
        "pause_reason": {"type": "string", "enum": ['', 'user', 'stopped']},
        "waiting_reason": {"type": "string", "enum": ['', 'environment']},
        "updated_at": {"type": ["number", "null"]}, "pending": {"type": "integer", "minimum": 0}, "running": {"type": "boolean"},
    }},
    "SetQueuePaused": {"type": "object", "required": ["paused"], "properties": {"paused": {"type": "boolean"}}},
    "SteerRun": {"type": "object", "required": ["text", "expected_turn"], "properties": {"text": {"type": "string"}, "expected_turn": {"type": "integer", "minimum": 1}, 'request_id': {'type': 'string', 'maxLength': 200}}},
    'SteerQueuedRun': {'type': 'object', 'required': ['expected_turn', 'expected_version'], 'properties': {
        'expected_turn': {'type': 'integer', 'minimum': 1},
        'expected_version': {'type': 'string', 'minLength': 64, 'maxLength': 64},
    }},
    "RunRecord": {"type": "object", "required": ["id", "session_id", "text", "status", "created_at"], "properties": {
        "requester": {"$ref": "#/components/schemas/HumanRequester"},
        "id": {"type": "string"}, "session_id": {"type": "string"}, "text": {"type": "string"},
        "kind": {"type": "string", "enum": ["prompt", "context_compact"]},
        "status": {"type": "string", "enum": ["queued", "running", "stopping", "completed", "failed", "interrupted"]},
        "created_at": {"type": "number"}, "request_id": {"type": ["string", "null"]}, "result": {"type": ["object", "null"]}, "error": {"anyOf": [{"$ref": "#/components/schemas/RunFailure"}, {"type": "null"}]},
        "started_at": {"type": ["number", "null"]}, "finished_at": {"type": ["number", "null"]}, "retry_of": {"type": ["string", "null"]},
        "goal_id": {"type": ["string", "null"]},
        "summary": {"type": "boolean"},
        "text_truncated": {"type": "boolean"},
        "prompt_input": {"type": "object", "properties": {"text": {"type": "string"}, "parts": {"type": "array", "items": {"type": "object"}}}},
        "team_route": {"$ref": "#/components/schemas/TeamRoute"},
        "task_text": {"type": "string"},
        "recovery": {"$ref": "#/components/schemas/RecoveryInfo"},
        "edit_version": {"type": "string"},
    }},
    "RunPage": {"type": "object", "required": ["runs", "has_more", "next_before"], "properties": {
        "runs": {"type": "array", "items": {"$ref": "#/components/schemas/RunRecord"}},
        "has_more": {"type": "boolean"}, "next_before": {"type": ["string", "null"]},
    }},
    "RecoveryInfo": {"type": "object", "required": ["mode"], "properties": {
        "mode": {"type": "string", "enum": ["inspect", "continue"]}, "source_version": {"type": "string"}, "verification_note": {"type": "string"}, "instruction_text": {"type": "string"},
    }},
    "RecoveryTool": {"type": "object", "required": ["call_id", "name", "effect", "idempotent", "status", "source_seq", "arguments", "result", "result_truncated"], "properties": {
        "call_id": {"type": "string"}, "name": {"type": "string"}, "effect": {"type": "string"}, "idempotent": {"type": "boolean"},
        "status": {"type": "string", "enum": ["unknown", "not_executed", "reported_error", "reported_success"]},
        "source_seq": {"type": "integer"}, "arguments": {"type": "string", "maxLength": 8000}, "result": {"type": "string", "maxLength": 2000}, "result_truncated": {"type": "boolean"},
    }},
    "RecoveryPreview": {"type": "object", "required": ["run_id", "session_id", "status", "turn", "task", "task_truncated", "error", "scope_known", "version", "tools", "total_tools", "uncertain_operations", "verification_required", "next_offset", "continuation_text", "inspection_text", "known_continuations"], "properties": {
        "run_id": {"type": "string"}, "session_id": {"type": "string"}, "status": {"type": "string"}, "turn": {"type": ["integer", "null"]},
        "task": {"type": "string", "maxLength": 16000}, "task_truncated": {"type": "boolean"}, "error": {"type": "string", "maxLength": 4000}, "error_copy": {"$ref": "#/components/schemas/ResourceCopy"},
        "scope_known": {"type": "boolean"}, "version": {"type": "string"}, "tools": {"type": "array", "items": {"$ref": "#/components/schemas/RecoveryTool"}},
        "total_tools": {"type": "integer"}, "uncertain_operations": {"type": "integer"}, "verification_required": {"type": "boolean"},
        "next_offset": {"type": ["integer", "null"]}, "continuation_text": {"type": "string"}, "inspection_text": {"type": "string"}, "known_continuations": {"type": "object"},
    }},
    "ContinueRun": {"type": "object", "properties": {"run_id": {"type": "string"}, "text": {"type": "string"},
        "mode": {"type": "string", "enum": ["inspect", "continue"]}, "recovery_version": {"type": "string"}, "verification_note": {"type": "string", "maxLength": 4000}}},
    "ApiError": {"type": "object", "required": ["error", "code"], "properties": {"error": {"type": "string"}, "code": {"type": "string"}, "details": {"type": ["object", "null"]}}},
}

SCHEMAS.update({
    "EncodingError": {"type": "object", "required": ["channel", "encoding", "offset", "reason"], "properties": {
        "channel": {"type": "string"}, "encoding": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}, "reason": {"type": "string"},
    }},
    "TerminalStatus": {"type": "object", "required": ["kind"], "properties": {
        "kind": {"type": "string", "enum": ["running", "exited"]}, "exit_code": {"type": ["integer", "null"]}, "signal": {"type": ["integer", "null"]},
    }},
    "TerminalOutputFailure": {"type": "object", "required": ["key", "params"], "additionalProperties": False, "properties": {
        "key": {"type": "string", "enum": ["ui.terminal.outputFailed"]},
        "params": {"type": "object", "required": ["error"], "additionalProperties": False, "properties": {"error": {"type": "string"}}},
    }},
    "TerminalView": {"type": "object", "required": ["sessionId", "type", "cwd", "pid", "status"], "properties": {
        "sessionId": {"type": "string"}, "type": {"type": "string"}, "cwd": {"type": "string"}, "pid": {"type": "integer"}, "name": {"type": "string"},
        "status": {"$ref": "#/components/schemas/TerminalStatus"},
        "transport": {"type": "string", "enum": ["pty", "pipe"]},
        "output_error": {"$ref": "#/components/schemas/TerminalOutputFailure"},
        "output_encoding": {"type": "string"}, "encoding_errors": {"type": "array", "items": {"$ref": "#/components/schemas/EncodingError"}},
        "control": {"type": "object", "required": ["state"], "properties": {"state": {"type": "string", "enum": ["stopping", "cleanup-failed"]}, "error": {"type": "string"}}},
    }},
    "TerminalOutput": {"type": "object", "required": ["sessionId", "text", "pendingText", "partialFirstLine", "totalLines", "lineBegin", "lineEnd", "truncated", "session", "terminal"], "properties": {
        "sessionId": {"type": "string"}, "text": {"type": "string", "maxLength": 32768}, "pendingText": {"type": "string", "maxLength": 32768},
        "totalLines": {"type": "integer", "minimum": 0}, "lineBegin": {"type": "integer", "minimum": 0}, "lineEnd": {"type": "integer", "minimum": 0},
        "partialFirstLine": {"type": "boolean"}, "truncated": {"type": "boolean"}, "session": {"$ref": "#/components/schemas/TerminalStatus"}, "terminal": {"$ref": "#/components/schemas/TerminalView"},
    }},
    "TerminalStop": {"type": "object", "required": ["sessionId", "outcome", "terminal"], "properties": {
        "sessionId": {"type": "string"}, "outcome": {"type": "string", "enum": ["closed", "already-closing", "cleanup-failed"]},
        "terminal": {"$ref": "#/components/schemas/TerminalView"}, "cleanupError": {"type": "string"}, "audit_error": {"type": "string"},
    }},
    "ScreenshotCapabilityReason": {"type": "object", "required": ["key"], "additionalProperties": False,
        "properties": {"key": {"type": "string", "enum": ["ui.screenshot.nativeUnavailable"]}}},
    "ScreenshotCapabilities": {"type": "object", "required": ["native", "hideWindow", "reason"], "additionalProperties": False, "properties": {
        "native": {"type": "boolean"}, "hideWindow": {"type": "boolean"},
        "reason": {"anyOf": [{"type": "null"}, {"$ref": "#/components/schemas/ScreenshotCapabilityReason"}]},
    }},
    "TerminalTypes": {"type": "object", "required": ["backends", "default_type", "enabled", "managed_runtime"], "properties": {
        "backends": {"type": "array", "items": {"type": "string"}}, "default_type": {"type": "string"}, "enabled": {"type": "boolean"}, "managed_runtime": {"type": "boolean"},
    }},
    "TerminalList": {"type": "object", "required": ["terminals", "backends", "default_type", "enabled", "managed_runtime"], "properties": {
        "terminals": {"type": "array", "items": {"$ref": "#/components/schemas/TerminalView"}},
        "backends": {"type": "array", "items": {"type": "string"}}, "default_type": {"type": "string"}, "enabled": {"type": "boolean"}, "managed_runtime": {"type": "boolean"},
    }},
    'TerminalRuntimeError': {'type': 'object', 'required': ['code', 'message'], 'properties': {
        'code': {'type': 'string'}, 'message': {'anyOf': [{'type': 'string'}, {'$ref': '#/components/schemas/ResourceCopy'}]}}},
    'TerminalRuntimeInstallation': {'type': 'object', 'required': ['id', 'status', 'phase', 'message', 'error', 'started_at', 'ended_at'], 'properties': {
        'id': {'type': 'string', 'format': 'uuid'}, 'status': {'type': 'string', 'enum': ['running', 'succeeded', 'failed', 'cancelled']},
        'phase': {'type': 'string', 'enum': ['detecting', 'dependency', 'verifying', 'complete']}, 'message': {'anyOf': [{'type': 'string'}, {'$ref': '#/components/schemas/ResourceCopy'}]},
        'error': {'anyOf': [{'$ref': '#/components/schemas/TerminalRuntimeError'}, {'type': 'null'}]},
        'started_at': {'type': 'number'}, 'ended_at': {'type': ['number', 'null']}}},
    'TerminalRuntimeSetup': {'type': 'object', 'required': ['supported', 'ready', 'error', 'installation'], 'properties': {
        'supported': {'type': 'boolean'}, 'ready': {'type': 'boolean'}, 'error': {'anyOf': [{'type': ['string', 'null']}, {'$ref': '#/components/schemas/ResourceCopy'}]},
        'installation': {'anyOf': [{'$ref': '#/components/schemas/TerminalRuntimeInstallation'}, {'type': 'null'}]}}},
    'TerminalRuntimeResult': {'type': 'object', 'required': ['installation'], 'properties': {
        'installation': {'anyOf': [{'$ref': '#/components/schemas/TerminalRuntimeInstallation'}, {'type': 'null'}]}}},
    'TerminalRuntimeInstall': {'type': 'object', 'required': ['request_id'], 'additionalProperties': False, 'properties': {
        'request_id': {'type': 'string', 'format': 'uuid', 'minLength': 36, 'maxLength': 36}}},
    'TerminalRuntimeCancel': {'type': 'object', 'required': ['id'], 'additionalProperties': False, 'properties': {
        'id': {'type': 'string'}}},
    "TerminalControl": {"type": "object", "required": ["terminal"], "properties": {
        "terminal": {"$ref": "#/components/schemas/TerminalView"}, "audit_error": {"type": "string"},
    }},
    "TerminalStream": {"type": "object", "required": ["text", "cursor", "start", "end", "reset", "eof", "terminal"], "properties": {
        "text": {"type": "string", "maxLength": 65536}, "cursor": {"type": "integer", "minimum": 0},
        "start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 0},
        "reset": {"type": "boolean"}, "eof": {"type": "boolean"}, "terminal": {"$ref": "#/components/schemas/TerminalView"},
    }},
    "TerminalInput": {"type": "object", "required": ["text"], "additionalProperties": False, "properties": {
        "text": {"type": "string", "minLength": 1, "maxLength": 16384}, "submit": {"type": "boolean", "default": False},
    }},
    "TerminalResize": {"type": "object", "required": ["cols", "rows"], "additionalProperties": False, "properties": {
        "cols": {"type": "integer", "minimum": 2, "maximum": 500}, "rows": {"type": "integer", "minimum": 1, "maximum": 300},
    }},
    "TerminalOpen": {"type": "object", "additionalProperties": False, "properties": {"type": {"type": "string", "maxLength": 32}, "output_encoding": {"type": "string"}}},
    "JobView": {"type": "object", "required": ["id", "kind", "label", "status", "startedAt"], "properties": {
        "completionScope": {"type": "string", "enum": ["terminal-wait"]},
        "terminalId": {"type": "string"},
        "terminalStatus": {"$ref": "#/components/schemas/TerminalStatus"},
        "wait": {"type": "object", "required": ["reason"], "properties": {"reason": {"type": "string"}, "detail": {"type": "string"}}},
        "id": {"type": "string"}, "kind": {"type": "string"}, "label": {"type": "string"},
        "status": {"type": "string", "enum": ["queued", "running", "stopping", "cleanup-failed", "completed", "killed", "failed", "unavailable"]},
        "startedAt": {"type": "integer"}, "finishedAt": {"type": "integer"}, "detail": {"type": "string"}, "cleanupError": {"type": "string"},
        "scope": {"type": "string", "enum": ["foreground-cleanup"]}, "callId": {"type": "string"},
        "turn": {"type": "integer"}, "step": {"type": "integer"}, "runId": {"type": "string"}, "pid": {"type": "integer"},
        "exitCode": {"type": "integer"}, "archived": {"type": "boolean"}, "recovered": {"type": "boolean"},
        "lastStatus": {"type": "string"}, "archiveError": {"type": "string"},
        "output_encoding": {"type": "string"}, "encoding_errors": {"type": "array", "items": {"$ref": "#/components/schemas/EncodingError"}},
    }},
    "JobHistory": {"type": "object", "required": ["jobs", "has_more", "next_before"], "properties": {
        "jobs": {"type": "array", "items": {"$ref": "#/components/schemas/JobView"}},
        "has_more": {"type": "boolean"}, "next_before": {"type": ["string", "null"]},
        "history_error": {"type": "string"},
    }},
    "JobOutput": {"type": "object", "required": ["job", "stdout", "stderr", "stdout_offset", "stderr_offset", "stdout_end", "stderr_end", "truncated"], "properties": {
        "job": {"$ref": "#/components/schemas/JobView"},
        "stdout": {"type": "string", "maxLength": 32768}, "stderr": {"type": "string", "maxLength": 32768},
        "stdout_offset": {"type": "integer", "minimum": 0}, "stderr_offset": {"type": "integer", "minimum": 0},
        "stdout_end": {"type": "integer", "minimum": 0}, "stderr_end": {"type": "integer", "minimum": 0},
        "truncated": {"type": "boolean"},
    }},
    "JobStop": {"type": "object", "required": ["outcome", "job"], "properties": {
        "outcome": {"type": "string", "enum": ["already-finished", "cancellation-requested", "cleanup-failed"]},
        "job": {"$ref": "#/components/schemas/JobView"}, "audit_error": {"type": "string"},
    }},
    "CommandOutput": {"type": "object", "required": ["type", "session_id", "call_id", "name", "output_seq", "stdout", "stderr", "stdout_offset", "stderr_offset"], "properties": {
        "type": {"type": "string", "enum": ["tool/output"]},
        "session_id": {"type": "string"}, "call_id": {"type": "string"}, "name": {"type": "string"},
        "output_seq": {"type": "integer", "minimum": 1},
        "stdout": {"type": "string", "maxLength": 32768}, "stderr": {"type": "string", "maxLength": 32768},
        "stdout_offset": {"type": "integer", "minimum": 0}, "stderr_offset": {"type": "integer", "minimum": 0},
        "run_id": {"type": ["string", "null"]}, "turn": {"type": "integer"}, "step": {"type": "integer"},
        "event_seq": {"type": "integer", "minimum": 0},
        "encoding_errors": {"type": "array", "items": {"$ref": "#/components/schemas/EncodingError"}},
    }},
    "QuestionAnswer": {"type": "object", "required": ["id", "selected"], "properties": {
        "id": {"type": "string"}, "selected": {"type": "array", "items": {"type": "string"}},
        "selected_ids": {"type": "array", "items": {"type": "string"}}, "custom": {"type": "string"},
    }},
    "SubmitAnswers": {"type": "object", "required": ["answers"], "properties": {
        "answers": {"type": "array", "items": {"$ref": "#/components/schemas/QuestionAnswer"}},
    }},
    "SubmissionStatus": {"type": "object", "required": ["run"], "properties": {
        "run": {"oneOf": [{"$ref": "#/components/schemas/RunRecord"}, {"type": "null"}]},
    }},
    "ChangedFile": {"type": "object", "required": ["path", "repo_path", "status", "untracked"], "properties": {
        "path": {"type": "string"}, "repo_path": {"type": "string"}, "old_path": {"type": ["string", "null"]},
        "status": {"type": "string"}, "untracked": {"type": "boolean"},
    }},
    "ReviewSummary": {"type": "object", "required": ["available", "files"], "properties": {
        "available": {"type": "boolean"}, "files": {"type": "array", "items": {"$ref": "#/components/schemas/ChangedFile"}},
        "root": {"type": "string"}, "workspace": {"type": "string"}, "scope": {"type": "string"},
        "total": {"type": "integer"}, "truncated": {"type": "boolean"}, "message": {"type": "string"},
    }},
    "ReviewPatch": {"type": "object", "required": ["path", "patch", "binary", "truncated", "added", "deleted"], "properties": {
        "path": {"type": "string"}, "patch": {"type": "string"}, "binary": {"type": "boolean"},
        "truncated": {"type": "boolean"}, "added": {"type": "integer"}, "deleted": {"type": "integer"},
    }},
    'FileOperationCounts': {'type': 'object', 'required': ['added', 'removed'], 'properties': {
        'added': {'type': 'integer'}, 'removed': {'type': 'integer'}}},
    'FileOperation': {'type': 'object', 'required': ['source_seq', 'call_id', 'name', 'path', 'command', 'time', 'truncated', 'counts', 'unavailable'], 'properties': {
        'source_seq': {'type': 'integer'}, 'call_id': {'type': 'string'}, 'name': {'type': 'string'}, 'path': {'type': 'string'},
        'command': {'type': 'string'}, 'time': {'type': 'number'}, 'truncated': {'type': 'boolean'},
        'counts': {'anyOf': [{'$ref': '#/components/schemas/FileOperationCounts'}, {'type': 'null'}]}, 'unavailable': {'type': ['string', 'null']}}},
    'TurnChangedFile': {'type': 'object', 'required': ['path', 'operations'], 'properties': {
        'path': {'type': 'string'}, 'operations': {'type': 'array', 'items': {'$ref': '#/components/schemas/FileOperation'}}}},
    'TurnChanges': {'type': 'object', 'required': ['session_id', 'turn', 'workspace_root', 'through', 'ended', 'files', 'total_operations', 'truncated', 'offset', 'next_offset'], 'properties': {
        'session_id': {'type': 'string'}, 'turn': {'type': 'integer'}, 'workspace_root': {'type': ['string', 'null']},
        'through': {'type': 'integer'}, 'ended': {'type': 'boolean'}, 'files': {'type': 'array', 'items': {'$ref': '#/components/schemas/TurnChangedFile'}},
        'total_operations': {'type': 'integer'}, 'truncated': {'type': 'boolean'}, 'offset': {'type': 'integer'}, 'next_offset': {'type': ['integer', 'null']}}},
    'FileOperationPatch': {'type': 'object', 'required': ['session_id', 'turn', 'source_seq', 'path', 'patch', 'truncated', 'counts', 'unavailable'], 'properties': {
        'session_id': {'type': 'string'}, 'turn': {'type': 'integer'}, 'source_seq': {'type': 'integer'}, 'path': {'type': 'string'},
        'patch': {'type': ['string', 'null']}, 'truncated': {'type': 'boolean'},
        'counts': {'anyOf': [{'$ref': '#/components/schemas/FileOperationCounts'}, {'type': 'null'}]}, 'unavailable': {'type': ['string', 'null']}}},
})



SCHEMAS['ComparisonFile'] = {'type': 'object', 'required': ['path', 'repo_path', 'old_path', 'status', 'added', 'deleted'], 'properties': {
    'path': {'type': 'string'}, 'repo_path': {'type': 'string'}, 'old_path': {'type': ['string', 'null']},
    'status': {'type': 'string'}, 'added': {'type': ['integer', 'null']}, 'deleted': {'type': ['integer', 'null']}}}
SCHEMAS['ComparisonPatch'] = {'type': 'object', 'required': ['path', 'patch', 'binary', 'truncated', 'conflict'], 'properties': {
    'path': {'type': 'string'}, 'patch': {'type': 'string'}, 'binary': {'type': 'boolean'},
    'truncated': {'type': 'boolean'}, 'conflict': {'type': 'boolean'}}}
SCHEMAS['Comparison'] = {'type': 'object', 'required': ['available', 'scope', 'left', 'right', 'revision', 'workspace', 'total', 'files', 'next_offset', 'added', 'deleted'], 'properties': {
    'available': {'type': 'boolean'}, 'scope': {'type': 'string'}, 'left': {'type': 'string'}, 'right': {'type': 'string'},
    'revision': {'type': 'string'}, 'workspace': {'type': 'string'}, 'total': {'type': 'integer'},
    'files': {'type': 'array', 'items': {'$ref': '#/components/schemas/ComparisonFile'}},
    'next_offset': {'type': ['integer', 'null']}, 'added': {'type': ['integer', 'null']}, 'deleted': {'type': ['integer', 'null']},
    'selection': {'$ref': '#/components/schemas/ComparisonPatch'}}}
SCHEMAS['ComparisonCommit'] = {'type': 'object', 'required': ['id', 'subject'], 'properties': {'id': {'type': 'string'}, 'subject': {'type': 'string'}}}
SCHEMAS['ComparisonOptions'] = {'type': 'object', 'required': ['branches', 'current', 'upstream', 'commits', 'next_offset'], 'properties': {
    'branches': {'type': 'array', 'items': {'type': 'string'}}, 'current': {'type': 'string'}, 'upstream': {'type': ['string', 'null']},
    'commits': {'type': 'array', 'items': {'$ref': '#/components/schemas/ComparisonCommit'}}, 'next_offset': {'type': ['integer', 'null']}}}

SCHEMAS['NetChangedFile'] = {'type': 'object', 'required': ['path', 'first_seq', 'last_seq', 'operations', 'available', 'kind', 'counts', 'truncated', 'reason'], 'properties': {
    'path': {'type': 'string'}, 'first_seq': {'type': 'integer'}, 'last_seq': {'type': 'integer'}, 'operations': {'type': 'integer'},
    'available': {'type': 'boolean'}, 'kind': {'type': 'string', 'enum': ['unknown', 'unchanged', 'created', 'modified']},
    'counts': {'anyOf': [{'$ref': '#/components/schemas/FileOperationCounts'}, {'type': 'null'}]}, 'truncated': {'type': 'boolean'}, 'reason': {'type': ['string', 'null']}}}
SCHEMAS['TurnNetChanges'] = {'type': 'object', 'required': ['session_id', 'turn', 'through', 'files', 'total_files', 'offset', 'next_offset', 'ended', 'uncertain_operations'], 'properties': {
    'session_id': {'type': 'string'}, 'turn': {'type': 'integer'}, 'through': {'type': 'integer'},
    'files': {'type': 'array', 'items': {'$ref': '#/components/schemas/NetChangedFile'}}, 'total_files': {'type': 'integer'},
    'offset': {'type': 'integer'}, 'next_offset': {'type': ['integer', 'null']}, 'ended': {'type': 'boolean'}, 'uncertain_operations': {'type': 'integer'}}}
SCHEMAS['NetFilePatch'] = {'type': 'object', 'required': SCHEMAS['NetChangedFile']['required'] + ['session_id', 'turn', 'through', 'patch'], 'properties': {
    **SCHEMAS['NetChangedFile']['properties'], 'session_id': {'type': 'string'}, 'turn': {'type': 'integer'}, 'through': {'type': 'integer'}, 'patch': {'type': ['string', 'null']}}}
SCHEMAS['FileRestoreReceipt'] = {'type': 'object', 'required': ['session_id', 'turn', 'source_seq', 'request_id', 'path', 'action', 'status', 'message', 'time'], 'properties': {
    'session_id': {'type': 'string'}, 'turn': {'type': 'integer'}, 'source_seq': {'type': 'integer'}, 'request_id': {'type': 'string'},
    'path': {'type': 'string'}, 'action': {'type': 'string', 'enum': ['restore', 'remove']},
    'status': {'type': 'string', 'enum': ['restored', 'rejected', 'unknown']}, 'message': {'type': 'string'}, 'time': {'type': 'number'}}}
SCHEMAS['FileRestorePreview'] = {'type': 'object', 'required': ['session_id', 'turn', 'source_seq', 'through', 'path', 'available', 'reason', 'action', 'patch', 'truncated', 'expected_version', 'latest_receipt'], 'properties': {
    'session_id': {'type': 'string'}, 'turn': {'type': 'integer'}, 'source_seq': {'type': 'integer'}, 'through': {'type': 'integer'},
    'path': {'type': 'string'}, 'available': {'type': 'boolean'}, 'reason': {'type': ['string', 'null']},
    'action': {'type': 'string', 'enum': ['none', 'restore', 'remove']}, 'patch': {'type': ['string', 'null']}, 'truncated': {'type': 'boolean'},
    'expected_version': {'type': ['string', 'null']}, 'latest_receipt': {'anyOf': [{'$ref': '#/components/schemas/FileRestoreReceipt'}, {'type': 'null'}]}}}
SCHEMAS['FileRestoreRequest'] = {'type': 'object', 'required': ['through', 'expected_version', 'request_id'], 'properties': {
    'through': {'type': 'integer'}, 'expected_version': {'type': 'string'}, 'request_id': {'type': 'string'}}}

SCHEMAS['LanguageSettings'] = {'type': 'object', 'required': ['language', 'effective_language', 'system_region', 'detection_source'], 'properties': {
    'language': {'type': 'string', 'enum': ['auto', 'zh-CN', 'en']},
    'effective_language': {'type': 'string', 'enum': ['zh-CN', 'en']},
    'system_region': {'type': ['string', 'null']}, 'detection_source': {'type': 'string'}}}
SCHEMAS['UiSettings'] = {'type': 'object', 'required': ['deepseek_web_enabled', 'deepseek_web_api_key_configured', 'brave_web_enabled', 'brave_web_api_key_configured'], 'properties': {
    **SCHEMAS['LanguageSettings']['properties'],
    'default_model': {'type': 'string'}, 'reasoning_effort': {'type': ['string', 'null']},
    'model_visibility': {'type': 'object', 'additionalProperties': {'type': 'boolean'}},
    'deepseek_web_enabled': {'type': 'boolean'}, 'deepseek_web_api_key_configured': {'type': 'boolean'}, 'deepseek_web_api_key_mask': {'type': 'string'},
    'brave_web_enabled': {'type': 'boolean'}, 'brave_web_api_key_configured': {'type': 'boolean'}, 'brave_web_api_key_mask': {'type': 'string'}}}
SCHEMAS['UiSettingsPatch'] = {'type': 'object', 'properties': {
    **{key: SCHEMAS['UiSettings']['properties'][key] for key in ('language', 'default_model', 'reasoning_effort', 'model_visibility', 'deepseek_web_enabled', 'brave_web_enabled')},
    'deepseek_web_api_key': {'type': 'string', 'writeOnly': True}, 'brave_web_api_key': {'type': 'string', 'writeOnly': True}}, 'additionalProperties': False}
SCHEMAS['UiSettingsResponse'] = {'type': 'object', 'required': ['settings'], 'properties': {
    'settings': {'$ref': '#/components/schemas/UiSettings'}, 'rejected': {'type': 'array', 'items': {'type': 'string'}}}}
SCHEMAS['SubagentDetail']['properties']['language'] = {'anyOf': [{'type': 'string', 'enum': ['zh-CN', 'en']}, {'type': 'null'}]}

SCHEMAS['SubagentView'] = {
    'type': 'object',
    'required': [key for key in SCHEMAS['SubagentDetail']['required'] if key != 'prompt'],
    'properties': {**{key: value for key, value in SCHEMAS['SubagentDetail']['properties'].items() if key != 'prompt'}, 'result_truncated': {'type': 'boolean'}},
}

SCHEMAS['SessionCompletion'] = {'type': 'object', 'required': ['seq', 'session_id', 'run_id', 'status'], 'properties': {
    'seq': {'type': 'integer', 'minimum': 1, 'maximum': 2**53 - 1},
    'session_id': {'type': 'string', 'minLength': 1}, 'run_id': {'type': 'string', 'minLength': 1},
    'status': {'type': 'string', 'enum': ['completed', 'failed', 'interrupted']},
}}
SCHEMAS['SessionActivity'] = {'type': 'object', 'required': ['running', 'catalog_revision', 'server_id', 'completion_cursor', 'completions_lost', 'completions'], 'properties': {
    'running': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}},
    'catalog_revision': {'type': 'integer', 'minimum': 0}, 'server_id': {'type': 'string', 'minLength': 1},
    'completion_cursor': {'type': 'integer', 'minimum': 0, 'maximum': 2**53 - 1},
    'completions_lost': {'type': 'boolean'},
    'completions': {'type': 'array', 'items': {'$ref': '#/components/schemas/SessionCompletion'}},
}}

SCHEMAS['HookActivity'] = {'type': 'object', 'required': ['id', 'root', 'session_id', 'call_id', 'turn', 'step', 'event', 'tool', 'label', 'status', 'started', 'duration_ms', 'source_seq', 'reason'], 'properties': {
    **{key: {'type': 'string'} for key in ('id', 'root', 'session_id', 'call_id', 'tool', 'label', 'reason')},
    'plugin_id': {'type': ['string', 'null']}, 'handler_type': {'type': 'string'},
    'event': {'type': 'string', 'enum': list(HOOK_EVENTS)},
    'status': {'type': 'string', 'enum': ['running', 'passed', 'blocked', 'failed', 'timed_out', 'cancelled', 'cleanup_failed', 'skipped', 'interrupted', 'stopped']},
    **{key: {'type': 'integer'} for key in ('turn', 'step', 'source_seq', 'duration_ms')},
    'started': {'type': 'number'}, 'finished': {'type': 'number'}}}
SCHEMAS['TurnArtifact'] = {'type': 'object', 'required': ['path', 'source_seq'], 'properties': {
    'path': {'type': 'string'}, 'source_seq': {'type': 'integer'},
    'workspace_root': {'type': ['string', 'null']}, 'name': {'type': 'string'}, 'description': {'type': 'string'}}}
SCHEMAS['TurnHookSummary'] = {'type': 'object', 'required': ['total', 'runs', 'blocked', 'unsuccessful', 'counts', 'duration_ms', 'rows', 'offset', 'next_offset'], 'properties': {
    **{key: {'type': 'integer'} for key in ('total', 'runs', 'blocked', 'unsuccessful', 'duration_ms', 'offset')},
    'counts': {'type': 'object', 'additionalProperties': {'type': 'integer'}},
    'rows': {'type': 'array', 'items': {'$ref': '#/components/schemas/HookActivity'}},
    'next_offset': {'type': ['integer', 'null']}}}
SCHEMAS['TurnVerificationCheck'] = {'type': 'object', 'required': ['check_id', 'status', 'source_seq', 'result_seq', 'paths'], 'properties': {
    'check_id': {'type': 'string'}, 'status': {'type': 'string', 'enum': ['passed', 'failed', 'stale', 'unknown']},
    'source_seq': {'type': 'integer'}, 'result_seq': {'type': 'integer'}, 'paths': {'type': 'array', 'items': {'type': 'string'}}}}
SCHEMAS['TurnDeliverySummary'] = {'type': 'object', 'required': ['run_id', 'start_seq', 'tool_executions', 'failed_tools', 'unknown_tools', 'artifacts', 'artifact_count', 'verification', 'checks'], 'properties': {
    'run_id': {'type': ['string', 'null']},
    **{key: {'type': 'integer', 'minimum': 0} for key in ('start_seq', 'tool_executions', 'failed_tools', 'unknown_tools', 'artifact_count')},
    'artifacts': {'type': 'array', 'maxItems': 32, 'items': {'$ref': '#/components/schemas/TurnArtifact'}},
    'checks': {'type': 'array', 'maxItems': 32, 'items': {'$ref': '#/components/schemas/TurnVerificationCheck'}},
    'verification': {'type': 'string', 'enum': ['unverified', 'passed', 'failed', 'stale', 'unknown']}}}
SCHEMAS['TurnCompletion'] = {'type': 'object', 'required': ['turn', 'end_seq', 'final_response_seq', 'response_seq', 'ended_at', 'feedback', 'hooks', 'delivery'], 'properties': {
    'turn': {'type': 'integer'}, 'end_seq': {'type': 'integer'},
    'final_response_seq': {'type': ['integer', 'null']}, 'response_seq': {'type': ['integer', 'null']},
    'ended_at': {'type': 'number'}, 'feedback': {'anyOf': [{'type': 'string', 'enum': ['positive', 'negative']}, {'type': 'null'}]},
    'delivery': {'$ref': '#/components/schemas/TurnDeliverySummary'},
    'hooks': {'$ref': '#/components/schemas/TurnHookSummary'}}}
SCHEMAS['HookEvidence'] = {'type': 'object', 'required': SCHEMAS['HookActivity']['required'] + ['command', 'stdout', 'stderr', 'fingerprint'], 'properties': {
    **SCHEMAS['HookActivity']['properties'],
    **{key: {'type': 'string'} for key in ('command', 'stdout', 'stderr', 'fingerprint')},
    'stdout_dropped': {'type': 'integer'}, 'stderr_dropped': {'type': 'integer'}, 'exit_code': {'type': ['integer', 'null']}}}
SCHEMAS['FileSearchMatch'] = {'type': 'object', 'required': ['path', 'absolute_path', 'kind'], 'properties': {
    'path': {'type': 'string'}, 'absolute_path': {'type': 'string'}, 'kind': {'type': 'string', 'enum': ['file', 'directory']}}}
SCHEMAS['FileSearchResult'] = {'type': 'object', 'required': ['session_id', 'root', 'query', 'matches', 'has_more', 'truncated', 'source'], 'properties': {
    'session_id': {'type': 'string'}, 'root': {'type': 'string'}, 'query': {'type': 'string'},
    'matches': {'type': 'array', 'maxItems': 100, 'items': {'$ref': '#/components/schemas/FileSearchMatch'}},
    'has_more': {'type': 'boolean'}, 'truncated': {'type': 'boolean'}, 'source': {'type': 'string', 'enum': ['git', 'filesystem']}}}


SCHEMAS['ToolResultReference'] = {'type': 'object', 'required': ['event_seq', 'result_index', 'total_chars', 'sha256'],
    'properties': {**{key: {'type': 'integer', 'minimum': 0} for key in ('event_seq', 'result_index', 'total_chars')},
                   'sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}}}
SCHEMAS['ToolResultPart'] = {'type': 'object', 'required': [
    'event_seq', 'result_index', 'total_chars', 'sha256', 'offset', 'next_offset', 'text', 'is_error', 'match_offset'],
    'properties': {**SCHEMAS['ToolResultReference']['properties'], 'offset': {'type': 'integer', 'minimum': 0},
                   'next_offset': {'type': ['integer', 'null'], 'minimum': 0}, 'text': {'type': 'string', 'maxLength': 8192},
                   'is_error': {'type': 'boolean'}, 'match_offset': {'type': ['integer', 'null'], 'minimum': 0}}}
SCHEMAS['ContextUsageTotals'] = {'type': 'object', 'required': [
    'input_tokens', 'output_tokens', 'reasoning_tokens', 'cache_read_tokens', 'cache_reported_input_tokens', 'total_tokens'],
    'properties': {key: {'type': 'integer', 'minimum': 0} for key in (
        'input_tokens', 'output_tokens', 'reasoning_tokens', 'cache_read_tokens', 'cache_reported_input_tokens', 'total_tokens')}}
SCHEMAS['ContextTreeCost'] = {'type': 'object', 'required': [
    'usage', 'requests', 'reported_requests', 'unreported_requests', 'compaction_requests',
    'cache_inconsistent_requests', 'session_count', 'body_bytes'], 'properties': {
        'usage': {'$ref': '#/components/schemas/ContextUsageTotals'},
        **{key: {'type': 'integer', 'minimum': 0} for key in (
            'requests', 'reported_requests', 'unreported_requests', 'compaction_requests',
            'cache_inconsistent_requests', 'session_count', 'body_bytes')}}}
SCHEMAS['ContextStatistics'] = {'type': 'object', 'required': [
    'usage', 'requests', 'reported_requests', 'unreported_requests', 'compaction_requests', 'cache_inconsistent_requests', 'tool_calls', 'tool_errors', 'subagents'],
    'properties': {'usage': {'$ref': '#/components/schemas/ContextUsageTotals'},
                   'tree_cost': {'$ref': '#/components/schemas/ContextTreeCost'},
                   **{key: {'type': 'integer', 'minimum': 0} for key in (
                       'requests', 'reported_requests', 'unreported_requests', 'compaction_requests', 'cache_inconsistent_requests', 'tool_calls', 'tool_errors')},
                   'subagents': {'type': ['integer', 'null'], 'minimum': 0}}}
SCHEMAS['ContextManagementSettings'] = {'type': 'object', 'required': [
    'context_auto_manage', 'context_working_budget', 'summary_max_tokens', 'output_reserve', 'context_window', 'tool_result_max_chars', 'tool_schema_mode'], 'properties': {
    'context_auto_manage': {'type': 'boolean', 'default': True},
    'context_working_budget': {'type': 'integer', 'minimum': 1, 'default': DEFAULT_WORKING_BUDGET},
    'tool_result_max_chars': {'type': ['integer', 'null'], 'minimum': 512, 'default': 12000},
    'tool_schema_mode': {'type': 'string', 'enum': ['auto', 'all'], 'default': 'auto'},
    'summary_max_tokens': {'type': ['integer', 'null'], 'minimum': 1, 'default': 8192},
    'output_reserve': {'type': 'integer', 'minimum': 0, 'default': 16000},
    'context_window': {'type': ['integer', 'null'], 'minimum': 1}}}
SCHEMAS['SettingsPatch'] = {'type': 'object', 'properties': {
    **SCHEMAS['ContextManagementSettings']['properties'], 'permission_policy': {'type': 'string'},
    **{name: {'type': ['integer', 'null'], 'minimum': 1} for name in (
        'max_steps', 'token_budget',
        'model_context_window', 'model_max_output_tokens')},
    'network_timeout': {'type': 'integer', 'minimum': 1}}, 'additionalProperties': False}
SCHEMAS['SettingsResponse'] = {'type': 'object', 'required': ['config', 'effective'], 'properties': {
    'config': {'$ref': '#/components/schemas/ContextManagementSettings'},
    'effective': {'type': 'object'}, 'tools': {'type': 'array', 'items': {'type': 'string'}},
    'skills': {'type': 'array', 'items': {'type': 'object'}}}}
SCHEMAS['ContextCurrent'] = {'type': 'object', 'required': [
    'through', 'event_count', 'surface_nodes', 'message_count', 'roles',
    'latest_request', 'latest_reported_request', 'statistics'], 'properties': {
        **{key: {'type': 'integer'} for key in ('through', 'event_count', 'surface_nodes', 'message_count')},
        'roles': {'type': 'object', 'additionalProperties': {'type': 'integer'}},
        'latest_request': {'type': ['object', 'null']}, 'latest_reported_request': {'type': ['object', 'null']},
        'tool_result_projection': {'$ref': '#/components/schemas/ToolResultProjectionSummary'},
        'statistics': {'$ref': '#/components/schemas/ContextStatistics'}}}
SCHEMAS['ToolResultProjectionSummary'] = {'type': 'object', 'required': [
    'results', 'abbreviated_results', 'original_chars', 'model_chars'], 'properties': {
    key: {'type': 'integer', 'minimum': 0} for key in (
        'results', 'abbreviated_results', 'original_chars', 'model_chars')}}
SCHEMAS['ContextAuditPage'] = {'type': 'object', 'required': [
    'session_id', 'through', 'current', 'compactions', 'has_more', 'next_before'], 'properties': {
        'session_id': {'type': 'string'}, 'through': {'type': 'integer'},
        'current': {'anyOf': [{'$ref': '#/components/schemas/ContextCurrent'}, {'type': 'null'}]},
        'compactions': {'type': 'array', 'items': {'type': 'object'}},
        'has_more': {'type': 'boolean'}, 'next_before': {'type': ['integer', 'null']}}}

SCHEMAS['RequestContentGroup'] = {'type': 'object', 'required': ['kind', 'bytes'], 'properties': {
    'kind': {'type': 'string', 'enum': ['input', 'tools', 'parameters', 'envelope']}, 'bytes': {'type': 'integer', 'minimum': 0}}}
SCHEMAS['RequestContentInput'] = {'type': 'object', 'required': ['index', 'role', 'path', 'bytes'], 'properties': {
    'index': {'type': 'integer', 'minimum': 0}, 'role': {'type': 'string'}, 'path': {'type': 'string'}, 'bytes': {'type': 'integer', 'minimum': 0}}}
SCHEMAS['RequestContentTool'] = {'type': 'object', 'required': ['index', 'name', 'description', 'bytes'], 'properties': {
    'index': {'type': 'integer', 'minimum': 0}, 'name': {'type': 'string'}, 'description': {'type': 'string'},
    'bytes': {'type': 'integer', 'minimum': 0}}}
SCHEMAS['RequestContentComposition'] = {'type': 'object', 'required': [
    'normalized_bytes', 'groups', 'largest_inputs', 'largest_tools'], 'properties': {
    'normalized_bytes': {'type': 'integer', 'minimum': 0},
    'groups': {'type': 'array', 'maxItems': 4, 'items': {'$ref': '#/components/schemas/RequestContentGroup'}},
    'largest_inputs': {'type': 'array', 'items': {'$ref': '#/components/schemas/RequestContentInput'}},
    'largest_tools': {'type': 'array', 'maxItems': 8, 'items': {'$ref': '#/components/schemas/RequestContentTool'}}}}
SCHEMAS['RequestToolDefinition'] = {'type': 'object', 'required': ['index', 'name', 'description'], 'properties': {
    'index': {'type': 'integer', 'minimum': 0}, 'name': {'type': 'string'}, 'description': {'type': 'string'}}}
SCHEMAS['RequestTraceDetailView'] = {'type': 'object', 'required': [
    'request', 'messages', 'tool_names', 'tool_definitions', 'parameters', 'cache_comparison', 'composition'], 'properties': {
    'request': {'type': 'object'}, 'messages': {'type': 'array', 'items': {'$ref': '#/components/schemas/RequestTraceInputView'}},
    'tool_names': {'type': 'array', 'items': {'type': 'string'}},
    'tool_definitions': {'type': 'array', 'items': {'$ref': '#/components/schemas/RequestToolDefinition'}},
    'parameters': {'type': 'object'},
    'cache_comparison': {'type': ['object', 'null']}, 'composition': {'$ref': '#/components/schemas/RequestContentComposition'}}}
SCHEMAS['RequestToolSource'] = {'type': 'object', 'required': ['call_id', 'tool', 'call_seq', 'event_seq', 'kind'], 'properties': {
    'call_id': {'type': 'string'}, 'tool': {'type': 'string'}, 'call_seq': {'type': 'integer', 'minimum': 0},
    'event_seq': {'type': 'integer', 'minimum': 0}, 'kind': {'type': 'string', 'enum': ['call', 'result']}}}
SCHEMAS['RequestPromptSource'] = {'type': 'object', 'required': ['event_seq', 'source', 'part', 'chars', 'bytes'], 'properties': {
    'event_seq': {'type': 'integer', 'minimum': 0}, 'source': {'type': 'string'},
    'part': {'type': 'string', 'enum': ['stable', 'runtime']}, 'chars': {'type': 'integer', 'minimum': 0},
    'bytes': {'type': 'integer', 'minimum': 0}}}
SCHEMAS['RequestTraceInputView'] = {'type': 'object', 'required': ['index', 'role', 'chars', 'preview', 'tool_sources', 'prompt_sources', 'prompt_source_count'], 'properties': {
    'index': {'type': 'integer', 'minimum': 0}, 'role': {'type': 'string'}, 'chars': {'type': 'integer', 'minimum': 0},
    'preview': {'type': 'string'}, 'tool_sources': {'type': 'array', 'maxItems': 8, 'items': {'$ref': '#/components/schemas/RequestToolSource'}},
    'prompt_sources': {'type': 'array', 'maxItems': 8, 'items': {'$ref': '#/components/schemas/RequestPromptSource'}},
    'prompt_source_count': {'type': 'integer', 'minimum': 0}}}
SCHEMAS['RequestTracePartView'] = {'type': 'object', 'required': ['text', 'offset', 'total_chars', 'next_offset'], 'properties': {
    'text': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0},
    'total_chars': {'type': 'integer', 'minimum': 0}, 'next_offset': {'type': ['integer', 'null']}}}


SCHEMAS.update({
    'PluginPackageSource': {'type': 'object', 'additionalProperties': False, 'required': ['path'], 'properties': {'path': {'type': 'string', 'minLength': 1, 'maxLength': 4096}, 'digest': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}},
    'PluginPackageInstall': {'type': 'object', 'additionalProperties': False, 'required': ['path', 'digest'], 'properties': {'path': {'type': 'string', 'minLength': 1, 'maxLength': 4096}, 'digest': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}},
    'PluginPackageUpdate': {'type': 'object', 'additionalProperties': False, 'required': ['path', 'digest', 'current_digest'], 'properties': {
        'path': {'type': 'string', 'minLength': 1, 'maxLength': 4096}, 'digest': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}, 'current_digest': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}},
    'PluginUpdateReview': {'type': 'object', 'required': ['current_digest', 'current_version', 'changed_connectors', 'removed_connectors', 'removed_skills', 'hooks_changed'], 'properties': {
        'current_digest': {'type': 'string'}, 'current_version': {'type': 'string'}, 'hooks_changed': {'type': 'boolean'},
        **{key: {'type': 'array', 'items': {'type': 'string'}} for key in ('changed_connectors', 'removed_connectors', 'removed_skills')}}},
    'PluginAppLink': {'type': 'object', 'additionalProperties': False, 'required': ['connector'], 'properties': {'connector': {'type': 'string', 'minLength': 1}}},
    'PluginAppReference': {'type': 'object', 'required': ['name', 'id', 'required'], 'properties': {
        'name': {'type': 'string'}, 'id': {'type': 'string'}, 'required': {'type': 'boolean'},
        'available': {'type': 'boolean'}, 'connected': {'type': 'boolean'}, 'status': {'type': 'string'},
        'connectors': {'type': 'array', 'items': {'type': 'string'}},
        'candidates': {'type': 'array', 'items': {'type': 'string'}}}},
    'PluginHookTrust': {'type': 'object', 'additionalProperties': False, 'required': ['fingerprint', 'trusted'], 'properties': {'fingerprint': {'type': 'string'}, 'trusted': {'type': 'boolean'}}},
    'PluginPackageHook': {'type': 'object', 'required': ['id', 'event', 'type', 'command', 'label', 'timeout'], 'properties': {
        'id': {'type': 'string'}, 'event': {'type': 'string'}, 'type': {'type': 'string'}, 'command': {'type': 'string'}, 'label': {'type': 'string'}, 'timeout': {'type': 'number'},
        'additionalContextLimit': {'type': 'integer', 'minimum': 0, 'maximum': 2147483647},
        'server': {'type': ['string', 'null']}, 'tool': {'type': ['string', 'null']}, 'input': {'type': 'object', 'additionalProperties': True}}},
    'PluginPackageSkill': {'type': 'object', 'required': ['name', 'description'], 'properties': {'name': {'type': 'string'}, 'description': {'type': 'string'}}},
    'PluginPackageConnector': {'type': 'object', 'required': ['name', 'transport'], 'properties': {
        'name': {'type': 'string'}, 'transport': {'type': 'string', 'enum': ['stdio', 'streamable-http']},
        'connector_name': {'type': 'string'}, 'connected': {'type': 'boolean'}, 'enabled': {'type': 'boolean'}, 'bound': {'type': 'boolean'}}},
    'PluginPackage': {'type': 'object', 'required': ['name', 'version', 'description', 'display_name', 'skills', 'connectors'], 'properties': {
        'name': {'type': 'string'}, 'version': {'type': 'string'}, 'description': {'type': 'string'}, 'display_name': {'type': 'string'},
        'skills': {'type': 'array', 'items': {'$ref': '#/components/schemas/PluginPackageSkill'}},
        'connectors': {'type': 'array', 'items': {'$ref': '#/components/schemas/PluginPackageConnector'}},
        'apps': {'type': 'array', 'items': {'$ref': '#/components/schemas/PluginAppReference'}}, 'dependencies_ready': {'type': 'boolean'},
        'hooks': {'type': 'array', 'items': {'$ref': '#/components/schemas/PluginPackageHook'}}, 'hooks_fingerprint': {'type': 'string'}, 'hooks_trusted': {'type': 'boolean'},
        'enabled': {'type': 'boolean'}, 'digest': {'type': 'string'}, 'path': {'type': 'string'}, 'update': {'$ref': '#/components/schemas/PluginUpdateReview'}}},
    'PluginPackageCatalog': {'type': 'object', 'required': ['packages'], 'properties': {'packages': {'type': 'array', 'items': {'$ref': '#/components/schemas/PluginPackage'}}}},
    'MarketplacePlugin': {'type': 'object', 'required': ['name', 'category', 'installation', 'authentication', 'source_type', 'path', 'error'], 'properties': {key: {'type': 'string'} for key in ('name', 'category', 'installation', 'authentication', 'source_type', 'path', 'error')}},
    'PluginMarketplace': {'type': 'object', 'required': ['name', 'display_name', 'path', 'fingerprint', 'plugins', 'registered', 'error'], 'properties': {
        **{key: {'type': 'string'} for key in ('name', 'display_name', 'path', 'fingerprint', 'error')}, 'registered': {'type': 'boolean'}, 'plugins': {'type': 'array', 'items': {'$ref': '#/components/schemas/MarketplacePlugin'}}}},
    'MarketplaceCatalog': {'type': 'object', 'required': ['marketplaces'], 'properties': {'marketplaces': {'type': 'array', 'items': {'$ref': '#/components/schemas/PluginMarketplace'}}}},
    'MarketplacePreview': {'type': 'object', 'additionalProperties': False, 'required': ['path', 'name', 'fingerprint'], 'properties': {key: {'type': 'string'} for key in ('path', 'name', 'fingerprint')}},
    'MarketplaceSource': {'type': 'object', 'additionalProperties': False, 'required': ['path'], 'properties': {'path': {'type': 'string', 'minLength': 1, 'maxLength': 4096}}},
    'PluginSourceDirectory': {'type': 'object', 'required': ['root', 'files', 'read_only', 'executable_supported'], 'properties': {
        'root': {'type': 'string'}, 'files': {'type': 'array', 'items': {'type': 'string'}}, 'read_only': {'type': 'boolean'}, 'executable_supported': {'type': 'boolean'}}},
    'PluginSourceFile': {'type': 'object', 'required': ['root', 'file', 'content', 'revision', 'executable', 'read_only'], 'properties': {
        'root': {'type': 'string'}, 'file': {'type': 'string'}, 'content': {'type': 'string'}, 'revision': {'type': 'string'}, 'executable': {'type': 'boolean'}, 'read_only': {'type': 'boolean'}}},
    'PluginSourceMutation': {'type': 'object', 'additionalProperties': False, 'required': ['path', 'file', 'revision'], 'properties': {
        'path': {'type': 'string', 'minLength': 1, 'maxLength': 4096}, 'file': {'type': 'string', 'minLength': 1, 'maxLength': 4096}, 'revision': {'type': 'string', 'pattern': '^(?:[a-f0-9]{64}|missing)$'}}},
    'PluginSourceSave': {'type': 'object', 'additionalProperties': False, 'required': ['path', 'file', 'revision', 'content', 'executable'], 'properties': {
        'path': {'type': 'string', 'minLength': 1, 'maxLength': 4096}, 'file': {'type': 'string', 'minLength': 1, 'maxLength': 4096}, 'revision': {'type': 'string', 'pattern': '^(?:[a-f0-9]{64}|missing)$'},
        'content': {'type': 'string', 'maxLength': 1048576}, 'executable': {'type': 'boolean'}}},
    'SkillCreate': {'type': 'object', 'additionalProperties': False, 'required': ['name', 'description', 'instructions'], 'properties': {
        'name': {'type': 'string', 'pattern': '^[a-z0-9]+(?:-[a-z0-9]+)*$', 'minLength': 1, 'maxLength': 64},
        'description': {'type': 'string', 'minLength': 1, 'maxLength': 1024},
        'instructions': {'type': 'string', 'minLength': 1, 'maxLength': 64000},
        'scope': {'type': 'string', 'enum': ['user', 'project']}, 'session_id': {'type': ['string', 'null']}}},
    'SkillCreated': {'type': 'object', 'required': ['path', 'name', 'description', 'content'], 'properties': {
        'path': {'type': 'string'}, 'name': {'type': 'string'}, 'description': {'type': 'string'}, 'content': {'type': 'string'}}},
    'PluginDraft': {'type': 'object', 'required': ['path', 'package'], 'properties': {'path': {'type': 'string'}, 'package': {'$ref': '#/components/schemas/PluginPackage'}}},
    'PluginDraftCreate': {'type': 'object', 'additionalProperties': False, 'required': ['path', 'name', 'description', 'instructions'], 'properties': {
        'path': {'type': 'string', 'minLength': 1, 'maxLength': 4096}, 'name': {'type': 'string', 'minLength': 1, 'maxLength': 256},
        'description': {'type': 'string', 'minLength': 1, 'maxLength': 4096}, 'instructions': {'type': 'string', 'minLength': 1, 'maxLength': 64000}}},
    'PluginPackageEnabled': {'type': 'object', 'additionalProperties': False, 'required': ['enabled'], 'properties': {'enabled': {'type': 'boolean'}}},
})


SCHEMAS.update({
    'BrowserAccessStatus': {'type': 'object', 'required': ['enabled', 'authenticated', 'subject', 'session_ids', 'credential_id', 'read_only'],
        'properties': {'enabled': {'type': 'boolean'}, 'authenticated': {'type': 'boolean'},
            'subject': {'type': ['string', 'null']}, 'credential_id': {'type': ['string', 'null']},
            'session_ids': {'type': 'array', 'items': {'type': 'string'}}, 'read_only': {'type': 'boolean'},
            'capabilities': {'type': 'object', 'additionalProperties': {'type': 'array', 'items': {'type': 'string'}}}}},
    'BrowserAccessLogin': {'type': 'object', 'additionalProperties': False, 'required': ['token'],
        'properties': {'token': {'type': 'string', 'minLength': 1, 'maxLength': 4096, 'writeOnly': True}}},
    'BrowserAccessSession': {'type': 'object', 'required': ['subject', 'session_ids', 'read_only'], 'properties': {
        'subject': {'type': 'string'}, 'session_ids': {'type': 'array', 'items': {'type': 'string'}},
        'read_only': {'type': 'boolean'}}},
    'BrowserAccessLogout': {'type': 'object', 'required': ['signed_out'],
        'properties': {'signed_out': {'type': 'boolean', 'const': True}}},
})


SCHEMAS.update({
    'PermissionApproval': deepcopy(APPROVAL_SCHEMA),
    'PermissionOperation': {**deepcopy(OPERATION_SCHEMA), 'properties': {
        **deepcopy(OPERATION_SCHEMA['properties']), 'approval': {'$ref': '#/components/schemas/PermissionApproval'}}},
    'SessionQuestionItem': {'type': 'object', 'required': ['id', 'question'], 'additionalProperties': True, 'properties': {
        'id': {'type': 'string'}, 'question': {'type': 'string'}, 'operation': {'$ref': '#/components/schemas/PermissionOperation'}}},
    'SessionQuestion': {'type': 'object', 'required': ['question_id', 'session_id', 'revision', 'questions'], 'properties': {
        'question_id': {'type': 'string'}, 'session_id': {'type': 'string'}, 'revision': {'type': 'string'},
        'questions': {'type': 'array', 'items': {'$ref': '#/components/schemas/SessionQuestionItem'}}}},
    'SessionQuestions': {'type': 'object', 'required': ['questions'], 'properties': {
        'questions': {'type': 'array', 'items': {'$ref': '#/components/schemas/SessionQuestion'}}}},
    'DecisionReceipt': {'type': 'object', 'required': ['question_id', 'request_id', 'actor', 'revision', 'status'], 'properties': {
        'question_id': {'type': 'string'}, 'request_id': {'type': 'string'}, 'actor': {'type': 'string'},
        'revision': {'type': 'string'}, 'status': {'type': 'string', 'const': 'recorded'}, 'resolved': {'type': 'boolean'}}},
    'DecisionStatus': {'type': 'object', 'required': ['receipt'], 'properties': {
        'receipt': {'anyOf': [{'$ref': '#/components/schemas/DecisionReceipt'}, {'type': 'null'}]}}},
    'SharedOmittedEvent': {'type': 'object', 'required': ['type', 'seq'], 'properties': {
        'type': {'type': 'string', 'const': 'shared/omitted'}, 'seq': {'type': 'integer'}, 'session_id': {'type': 'string'}}},
    'HumanRequester': {'type': 'object', 'required': ['subject', 'display_name'], 'properties': {
        'subject': {'type': 'string'}, 'display_name': {'type': 'string'}}},
    'InvitationCreated': {'type': 'object', 'required': ['id', 'token', 'role', 'expires_at'], 'properties': {
        'id': {'type': 'string'}, 'token': {'type': 'string', 'writeOnly': True},
        'role': {'type': 'string', 'enum': ['observer', 'collaborator', 'administrator']}, 'expires_at': {'type': 'integer'}}},
    'InvitationRevoked': {'type': 'object', 'required': ['revoked'], 'properties': {'revoked': {'type': 'boolean'}}},
    'InvitationClaimStatus': {'type': 'object', 'required': ['id', 'status'], 'properties': {
        'id': {'type': 'string'}, 'status': {'type': 'string', 'enum': ['pending', 'approved', 'revoked']}, 'subject': {'type': 'string'}}},
    'SessionMemberProfile': {'type': 'object', 'required': ['display_name'], 'properties': {'display_name': {'type': 'string'}}},
    'SessionInvitation': {'type': 'object', 'required': ['id', 'role', 'status', 'expires_at'], 'properties': {
        'id': {'type': 'string'}, 'role': {'type': 'string'}, 'status': {'type': 'string'},
        'expires_at': {'type': 'integer'}, 'display_name': {'type': 'string'}, 'subject': {'type': 'string'}}},
    'SessionMemberAccess': {'type': 'object', 'required': ['session_id', 'owner', 'revision', 'members', 'profiles'],
        'properties': {'session_id': {'type': 'string'}, 'owner': {'type': ['string', 'null']}, 'revision': {'type': 'integer'},
            'members': {'type': 'object', 'additionalProperties': {'type': 'array', 'items': {'type': 'string'}}},
            'profiles': {'type': 'object', 'additionalProperties': {'$ref': '#/components/schemas/SessionMemberProfile'}}}},
    'SessionCollaboration': {'type': 'object', 'required': ['access', 'invitations'], 'properties': {
        'origin': {'type': ['string', 'null']}, 'access': {'$ref': '#/components/schemas/SessionMemberAccess'},
        'invitations': {'type': 'array', 'items': {'$ref': '#/components/schemas/SessionInvitation'}}}},
})


SCHEMAS.update(ACADEMY_SCHEMAS)
SCHEMAS.update(SUPERAGENT_SCHEMAS)

# 说明文案只在读取协议时解析，避免模块导入语言冻结整个目录。
_SCHEMA_DESCRIPTION_KEYS = {
    ('SetSessionPermission', 'permission_policy'): 'python.protocol.7fda4c42e659',
    ('ConversationHistory', 'through'): 'python.protocol.f46f03a1ca79',
    ('ConversationHistory', 'total'): 'python.protocol.129ef956f495',
    ('RunRecord', 'summary'): 'python.protocol.9ffe2384b7f1',
    ('RunRecord', 'task_text'): 'python.protocol.ba963d2a85c2',
    ('RunRecord', 'edit_version'): 'python.protocol.34b349b5d696',
    ('CommandOutput',): 'python.protocol.7be989ba5663',
}


def _localized_schemas() -> dict[str, dict[str, Any]]:
    """每次返回独立机器协议快照，并按当前语言补齐自有说明。"""
    schemas = deepcopy(SCHEMAS)
    for path, key in _SCHEMA_DESCRIPTION_KEYS.items():
        target = schemas[path[0]]
        for property_name in path[1:]:
            target = target['properties'][property_name]
        target['description'] = tr(key)
    return schemas


# 供应商配置采用接口级凭据；模型列表与 Key 池不再相互嵌套。
SCHEMAS.update({
    'ProviderAuth': {'type': 'object', 'required': ['type', 'env'], 'properties': {
        'type': {'type': 'string', 'enum': ['api_key', 'oauth', 'environment', 'opencode_local', 'anonymous']},
        'env': {'type': 'object', 'additionalProperties': {'type': 'string'}}, 'configured': {'type': 'boolean'}, 'authorization_id': {'type': 'string', 'writeOnly': True}}},
    'ProviderModel': {'type': 'object', 'required': ['id', 'model', 'name', 'enabled', 'input'], 'properties': {
        'id': {'type': 'string'}, 'model': {'type': 'string'}, 'name': {'type': 'string'}, 'enabled': {'type': 'boolean'},
        'input': {'type': 'array', 'minItems': 1, 'items': {'type': 'string', 'enum': ['text', 'image', 'video']}},
        'context_window': {'type': 'integer', 'minimum': 1}, 'max_output_tokens': {'type': 'integer', 'minimum': 1},
        'reasoning_efforts': {'anyOf': [{'type': 'object'}, {'type': 'boolean'}]}, 'compat': {'type': 'object'}}},
    'ProviderKey': {'type': 'object', 'required': ['id', 'enabled'], 'properties': {
        'id': {'type': 'string'}, 'enabled': {'type': 'boolean'}, 'mask': {'type': 'string'},
        'available': {'type': 'boolean'}, 'current': {'type': 'boolean'}, 'in_use': {'type': 'boolean'},
        'value': {'type': 'string', 'writeOnly': True}}},
    'ProviderConnection': {'type': 'object', 'required': ['id', 'name', 'protocol', 'base_url', 'enabled', 'auth', 'keys', 'models'], 'properties': {
        'id': {'type': 'string'}, 'name': {'type': 'string'}, 'protocol': {'type': 'string'}, 'base_url': {'type': 'string'}, 'enabled': {'type': 'boolean'},
        'auth': {'$ref': '#/components/schemas/ProviderAuth'},
        'keys': {'type': 'array', 'items': {'$ref': '#/components/schemas/ProviderKey'}},
        'models': {'type': 'array', 'items': {'$ref': '#/components/schemas/ProviderModel'}},
        'model_catalog': {'type': 'array', 'items': {'type': 'object'}},
        'headers': {'type': 'object'}, 'compat': {'type': 'object'}, 'timeout_ms': {'type': 'integer', 'minimum': 1}, 'max_retries': {'type': 'integer', 'minimum': 0}}},
    'ModelProvider': {'type': 'object', 'required': ['id', 'type', 'name', 'connections'], 'properties': {
        'id': {'type': 'string'}, 'type': {'type': 'string'}, 'name': {'type': 'string'},
        'connections': {'type': 'array', 'items': {'$ref': '#/components/schemas/ProviderConnection'}}}},
    'ProviderRouting': {'type': 'object', 'required': ['version', 'revision', 'providers'], 'properties': {
        'version': {'type': 'integer', 'enum': [2]}, 'revision': {'type': 'string'},
        'providers': {'type': 'array', 'items': {'$ref': '#/components/schemas/ModelProvider'}},
        'models': {'type': 'array', 'items': {'type': 'object'}}, 'current': {'type': 'null'}, 'cycle': {'type': 'integer'}}},
    'ProviderAuthorization': {'type': 'object', 'required': ['id', 'connection_id', 'status', 'events', 'prompt', 'error'], 'properties': {
        'id': {'type': 'string'}, 'connection_id': {'type': 'string'}, 'status': {'type': 'string'}, 'events': {'type': 'array', 'items': {'type': 'object'}},
        'prompt': {'anyOf': [{'type': 'object'}, {'type': 'null'}]}, 'error': {'type': 'string'}}},
})
SCHEMAS['SetConnection'] = {'type': 'object', 'additionalProperties': False, 'required': ['providers', 'revision'], 'properties': {
    'revision': {'type': 'string'}, 'providers': {'type': 'array', 'items': {'$ref': '#/components/schemas/ModelProvider'}}}}
SCHEMAS['SetConnectionKeyStatus'] = {'type': 'object', 'additionalProperties': False, 'required': ['connection_id', 'key_id', 'enabled'], 'properties': {
    'connection_id': {'type': 'string'}, 'key_id': {'type': 'string'}, 'enabled': {'type': 'boolean'}}}

# 输入草稿允许由运行时填充默认值，不能套用要求完整状态的响应字段集。
for name, required in (('ProviderKey', ['id']), ('ProviderModel', ['id']), ('ProviderConnection', ['id'])):
    SCHEMAS[name + 'Draft'] = {**deepcopy(SCHEMAS[name]), 'required': required}
SCHEMAS['ProviderConnectionDraft']['properties']['keys']['items'] = {'$ref': '#/components/schemas/ProviderKeyDraft'}
SCHEMAS['ProviderConnectionDraft']['properties']['models']['items'] = {'$ref': '#/components/schemas/ProviderModelDraft'}


def openapi(templates: list[str]) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for entry in templates:
        method, path = entry.split(" ", 1)
        operation: dict[str, Any] = {"operationId": method.lower() + "_" + re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_"), "responses": {"200": {"description": tr('python.protocol.38de25e52295')}, "400": {"description": tr('python.protocol.2ac42777a9fe'), "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}}}}
        parameters = [{"name": name, "in": "path", "required": True, "schema": {"type": "string"}} for name in re.findall(r"{([^}]+)}", path)]
        parameters.append({"name": "X-Request-ID", "in": "header", "schema": {"type": "string"}})
        collaboration_response = None
        if path == '/api/v1/status' and method == 'GET':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/ModelCredentialStatus'}}}
        if path == '/api/v1/workspaces' and method == 'GET':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/WorkspaceCatalog'}}}
        if path == '/api/v1/workspace-options' and method == 'GET':
            parameters.append({'name': 'path', 'in': 'query', 'required': True, 'schema': {'type': 'string', 'minLength': 1}})
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/WorkspaceOptions'}}}
        if path == '/api/v1/sessions' and method == 'POST':
            operation['requestBody'] = {'required': False, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/NewConversationRequest'}}}}
            operation['responses']['503'] = {'description': tr('ui.newConversation.preparationUnconfirmed'), 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ApiError'}}}}
        if path.startswith('/api/v1/sessions/') and '/questions/' in path and path.endswith('/answer'):
            collaboration_response = 'DecisionReceipt'
        elif path.startswith('/api/v1/sessions/') and '/decisions/' in path:
            collaboration_response = 'DecisionStatus'
        elif path.startswith('/api/v1/sessions/') and path.endswith('/questions'):
            collaboration_response = 'SessionQuestions'
        elif path.endswith('/collaboration'):
            collaboration_response = 'SessionCollaboration'
        elif path.endswith('/collaboration/enable') or '/members/' in path or path.endswith('/approve') and '/invitations/' in path:
            collaboration_response = 'SessionMemberAccess'
        elif path.endswith('/revoke') and '/invitations/' in path:
            collaboration_response = 'InvitationRevoked'
        elif path.endswith('/invitations') and method == 'POST':
            collaboration_response = 'InvitationCreated'
        elif path in ('/api/v1/access/invitations/claim', '/api/v1/access/invitations/status'):
            collaboration_response = 'InvitationClaimStatus'
        elif path == '/api/v1/access/invitations/enter':
            collaboration_response = 'BrowserAccessSession'
        if collaboration_response:
            operation['responses']['200']['content'] = {'application/json': {'schema': {
                '$ref': '#/components/schemas/' + collaboration_response}}}
        invitation_fields = None
        if path.startswith('/api/v1/sessions/') and '/questions/' in path and path.endswith('/answer'):
            invitation_fields = ({'request_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{16,128}$'},
                'expected_revision': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
                'answers': {'type': 'array', 'items': {'$ref': '#/components/schemas/QuestionAnswer'}}},
                ['request_id', 'expected_revision', 'answers'])
        elif path.endswith('/invitations') and method == 'POST':
            invitation_fields = ({'role': {'type': 'string', 'enum': ['observer', 'collaborator', 'administrator']},
                                  'expires_in': {'type': 'integer', 'minimum': 1, 'maximum': 604800}}, [])
        elif path.startswith('/api/v1/access/invitations/'):
            fields = {'token': {'type': 'string', 'maxLength': 4096, 'writeOnly': True},
                      'claim_secret': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{43}$', 'writeOnly': True}}
            if path.endswith('/claim'):
                fields['display_name'] = {'type': 'string', 'minLength': 1, 'maxLength': 80}
            invitation_fields = (fields, list(fields))
        elif path.endswith('/approve') and '/invitations/' in path:
            invitation_fields = ({'expected_revision': {'type': 'integer'}}, ['expected_revision'])
        elif '/members/' in path:
            invitation_fields = ({'role': {'type': 'string', 'enum': ['observer', 'collaborator', 'administrator', 'removed']},
                                  'expected_revision': {'type': 'integer'}}, ['role', 'expected_revision'])
        elif path.endswith('/collaboration/enable') or path.endswith('/revoke') and '/invitations/' in path:
            invitation_fields = ({}, [])
        if invitation_fields:
            fields, required = invitation_fields
            operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {
                'type': 'object', 'additionalProperties': False, 'properties': fields, 'required': required}}}}
        if path == '/api/v1/access/status':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/BrowserAccessStatus'}}}
        if path in ('/api/v1/access/login', '/api/v1/access/logout'):
            login = path.endswith('/login')
            operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema':
                {'$ref': '#/components/schemas/BrowserAccessLogin'} if login else
                {'type': 'object', 'additionalProperties': False, 'properties': {}}}}}
            operation['responses']['200']['content'] = {'application/json': {'schema': {
                '$ref': '#/components/schemas/' + ('BrowserAccessSession' if login else 'BrowserAccessLogout')}}}
            operation['responses']['200']['headers'] = {'Set-Cookie': {'schema': {'type': 'string'}}}
            for status in ('401', '403', '404'):
                operation['responses'][status] = {'description': tr('protocol.access_rejected'), 'content': {
                    'application/json': {'schema': {'$ref': '#/components/schemas/ApiError'}}}}
            parameters.append({'name': 'Origin', 'in': 'header', 'required': True, 'schema': {'type': 'string'}})
        if path == '/api/v1/project-board' or path.startswith('/api/v1/project-board/tasks') and not path.endswith('/launch'):
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/ProjectBoard'}}}
            parameters.append({'name': 'root', 'in': 'query', 'schema': {'type': 'string'}})
            if method in ('POST', 'PATCH'):
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {
                    'type': 'object', 'additionalProperties': False, 'required': ['revision', 'task'], 'properties': {
                        'revision': {'type': 'string'}, 'task': {'type': 'object', 'additionalProperties': False,
                            'properties': {key: value for key, value in TASK_SCHEMA['properties'].items() if key != 'verification_result'}}}}}}}
        if method == 'GET' and path == '/api/v1/sessions/{session_id}/context':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/ContextAuditPage'}}}
        if method == 'GET' and path == '/api/v1/sessions/{session_id}/tool-results/{source_seq}':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/ToolResultPart'}}}
            for parameter in parameters:
                if parameter['name'] == 'source_seq':
                    parameter['schema'] = {'type': 'integer', 'minimum': 0}
            for status in ('404', '409'):
                operation['responses'][status] = {'description': tr('protocol.evidence_missing_changed'),
                    'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ApiError'}}}}
            parameters.extend([
                {'in': 'query', 'name': key, 'schema': schema} for key, schema in {
                    'index': {'type': 'integer', 'minimum': 0}, 'offset': {'type': 'integer', 'minimum': 0},
                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 8192},
                    'sha256': {'type': 'string'}, 'q': {'type': 'string', 'minLength': 1, 'maxLength': 256},
                    'agent_id': {'type': 'string'}}.items()])
            parameters.extend([{'name': name, 'in': 'query', 'schema': {'type': 'integer'}} for name in ('through', 'before')])
        if path.startswith('/api/v1/plugin-packages'):
            response_type = 'PluginPackageCatalog' if path == '/api/v1/plugin-packages' or path.endswith(('/enabled', '/hooks/trust', '/link')) else 'PluginPackage'
            if path.endswith(('/create', '/draft')):
                response_type = 'PluginDraft'
            if path.endswith('/draft'):
                parameters.extend([{'name': name, 'in': 'query', 'required': True, 'schema': {'type': 'string'}} for name in ('path', 'name')])
            if method != 'DELETE':
                operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/' + response_type}}}
            if method == 'POST':
                request_type = 'PluginAppLink' if path.endswith('/link') else 'PluginHookTrust' if path.endswith('/hooks/trust') else 'PluginPackageEnabled' if path.endswith('/enabled') else ('PluginPackageInstall' if path.endswith('/install') else 'PluginPackageSource')
                if path.endswith('/create'):
                    request_type = 'PluginDraftCreate'
                elif path.endswith('/update'):
                    request_type = 'PluginPackageUpdate'
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/' + request_type}}}}
            if path in ('/api/v1/plugin-packages/source', '/api/v1/plugin-packages/source/file'):
                response_type = 'PluginSourceDirectory' if path.endswith('/source') or method == 'DELETE' else 'PluginSourceFile'
                operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/' + response_type}}}
                if method == 'GET':
                    parameters.extend([{'name': name, 'in': 'query', 'required': True, 'schema': {'type': 'string'}} for name in (('path',) if path.endswith('/source') else ('path', 'file'))])
                else:
                    request_type = 'PluginSourceSave' if method == 'PATCH' else 'PluginSourceMutation'
                    operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/' + request_type}}}}
        if path in ('/api/v1/plugins/skills/create', '/api/v1/plugins/skills/created'):
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/SkillCreated'}}}
            if method == 'POST':
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/SkillCreate'}}}}
            else:
                parameters.extend([{'name': name, 'in': 'query', 'required': name == 'name', 'schema': {'type': 'string'}} for name in ('name', 'scope', 'session_id')])
        if path.startswith('/api/v1/plugin-marketplaces'):
            preview = path.endswith('/preview')
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/' + ('PluginDraft' if preview else 'MarketplaceCatalog')}}}
            if method != 'GET':
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/' + ('MarketplacePreview' if preview else 'MarketplaceSource')}}}}
        if path == '/api/v1/plugins/agent-selection':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/AgentSelectionResponse'}}}
            if method == 'POST':
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/SetAgentSelection'}}}}
            else:
                parameters.append({'name': 'session_id', 'in': 'query', 'required': True, 'schema': {'type': 'string'}})
        if path == '/api/v1/plugins/agents/create' and method == 'POST':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/AgentCreatedResponse'}}}
            operation['requestBody'] = {'required': True, 'content': {'application/json': {
                'schema': {'$ref': '#/components/schemas/AgentDefinitionInput'}}}}
        if path == '/api/v1/plugins/agents/{name}' and method == 'PATCH':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/AgentUpdatedResponse'}}}
            operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {
                'allOf': [{'$ref': '#/components/schemas/AgentDefinitionInput'}, {'type': 'object',
                    'required': ['expected_revision'], 'properties': {'expected_revision': {'type': 'string'}}}]}}}}
        if method == 'POST' and path == '/api/v1/sessions/{session_id}/fork':
            operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {
                'type': 'object', 'properties': {'through_seq': {'type': 'integer', 'minimum': -1},
                    'before_message_seq': {'type': 'integer', 'minimum': 0}, 'title': {'type': 'string'}},
                'not': {'required': ['through_seq', 'before_message_seq']}}}}}
            operation['responses']['201'] = {'description': tr('python.protocol.38de25e52295')}
        if path.endswith('/files/search'):
            parameters.extend([{'name': 'q', 'in': 'query', 'schema': {'type': 'string', 'maxLength': 256, 'default': ''}},
                {'name': 'limit', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 40}},
                {'name': 'refresh', 'in': 'query', 'schema': {'type': 'string', 'enum': ['0', '1'], 'default': '0'}}])
            operation['description'] = tr('python.protocol.55ad47c4e223')
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/FileSearchResult'}}}
            operation['responses']['404'] = {'description': tr('python.protocol.2d98325ea8eb')}
            operation['responses']['503'] = {'description': tr('python.protocol.c9a105fe3584')}
        if path.endswith('/hooks/{hook_id}'):
            parameters.append({'name': 'source_seq', 'in': 'query', 'required': True, 'schema': {'type': 'integer', 'minimum': 0, 'maximum': 2**53 - 1}})
            operation['responses']['200']['content'] = {'application/json': {'schema': {'type': 'object', 'required': ['hook'], 'properties': {'hook': {'$ref': '#/components/schemas/HookEvidence'}}}}}
            operation['responses']['404'] = {'description': tr('python.protocol.f6023ec63cb4')}
        if path.startswith('/api/v1/hooks'):
            parameters.append({'name': 'scope', 'in': 'query', 'schema': {'type': 'string', 'enum': ['project', 'user']}})
            parameters.append({'name': 'root', 'in': 'query', 'schema': {'type': 'string'}, 'description': tr('python.protocol.a897a0444cbd')})
            operation['responses']['403'] = {'description': tr('python.protocol.8faa7907b318')}
            operation['responses']['503'] = {'description': tr('python.protocol.187cd199b13a')}
            snapshot = {'type': 'object', 'required': ['root', 'source', 'fingerprint', 'document', 'rows', 'error', 'trusted', 'runs'], 'properties': {
                'root': {'type': 'string'}, 'source': {'type': 'string'}, 'fingerprint': {'type': 'string'},
                'document': {'type': 'object'}, 'text': {'type': 'string'}, 'rows': {'type': 'array', 'items': {'type': 'object'}},
                'error': {'type': 'string'}, 'trusted': {'type': 'boolean'}, 'runs': {'type': 'array', 'items': {'type': 'object'}}}}
            operation['responses']['200']['content'] = {'application/json': {'schema': snapshot}}
            if method == 'POST':
                document = {'type': 'object', 'required': ['hooks'], 'description': tr('python.protocol.461f3619edcf'), 'properties': {'hooks': {'type': 'object'}}}
                fingerprint = {'type': 'string', 'description': tr('python.protocol.12bc2dcde5b7')}
                if path.endswith('/preview'):
                    properties = {'document': document, 'event': {'type': 'string', 'enum': list(HOOK_EVENTS)}, 'tool': {'type': 'string', 'minLength': 1, 'maxLength': 256}}
                    response = {'type': 'object', 'required': ['event', 'tool', 'matches', 'executed'], 'properties': {'event': properties['event'], 'tool': properties['tool'], 'matches': {'type': 'array', 'items': {'type': 'object'}}, 'executed': {'const': False}}}
                    operation['responses']['200']['content'] = {'application/json': {'schema': response}}
                else:
                    properties = {'expected_fingerprint': fingerprint}
                    properties.update({'trusted': {'type': 'boolean'}} if path.endswith('/trust') else {'document': document})
                    operation['responses']['409'] = {'description': tr('python.protocol.4bc65b9128d9')}
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'type': 'object', 'required': list(properties), 'properties': properties}}}}
        if path.endswith('/plans'):
            operation['responses']['200']['content'] = {'application/json': {'schema': {'type': 'object', 'required': ['plans'], 'properties': {'plans': {'type': 'array', 'items': {'$ref': '#/components/schemas/PlanVersion'}}}}}}
            operation['responses']['404'] = {'description': tr('python.protocol.2d98325ea8eb')}
        if path.endswith('/plans/{plan_id}'):
            operation['responses']['200']['content'] = {'application/json': {'schema': {'type': 'object', 'required': ['plan'], 'properties': {'plan': {'$ref': '#/components/schemas/PlanDetail'}}}}}
            operation['responses']['404'] = {'description': tr('python.protocol.3c89b1cbb627')}
        if path.endswith('/plans/{plan_id}/evidence/{source_seq}'):
            parameters.extend([{'name': 'offset', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 0, 'default': 0}},
                               {'name': 'limit', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 1, 'maximum': 20000, 'default': 20000}}])
            operation['responses']['200']['content'] = {'application/json': {'schema': {'type': 'object', 'required': ['evidence'], 'properties': {'evidence': {'$ref': '#/components/schemas/PlanEvidencePage'}}}}}
            operation['responses']['404'] = {'description': tr('python.protocol.ea072e932613')}
        if path in {'/api/v1/mcp-service', '/api/v1/mcp-service/start', '/api/v1/mcp-service/stop', '/api/v1/mcp-service/companion'}:
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/McpServiceState'}}}
        if path == '/api/v1/mcp-service/start':
            operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/McpServiceStart'}}}}
        if path == '/api/v1/mcp-service/password':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'type': 'object', 'required': ['password'], 'properties': {'password': {'type': 'string'}}}}}
        if path == '/api/v1/mcp-service/companion':
            operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ChatCompanionSettings'}}}}
        if path == '/api/v1/mcp-service/companion/token':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'type': 'object', 'required': ['token'], 'properties': {'token': {'type': 'string'}}}}}
        if path == '/api/v1/chatgpt' or path.startswith('/api/v1/chatgpt/'):
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/ChatGPTAccountStatus'}}}
        if path in {'/api/v1/connection', '/api/v1/connection/status', '/api/v1/connection/reset'}:
            response_schema = {'$ref': '#/components/schemas/ProviderRouting'}
            if method == 'PATCH' and path.endswith('/connection'):
                response_schema = {'type': 'object', 'required': ['config', 'routing'], 'properties': {'config': {'type': 'object'}, 'routing': response_schema}}
            operation['responses']['200']['content'] = {'application/json': {'schema': response_schema}}
            if method == 'PATCH':
                schema_name = 'SetConnectionKeyStatus' if path.endswith('/status') else 'SetConnection'
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/' + schema_name}}}}
        if path.startswith('/api/v1/connection/authorization'):
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/ProviderAuthorization'}}}
        if path == '/api/v1/connection/providers':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'type': 'object', 'required': ['providers'], 'properties': {'providers': {'type': 'array', 'items': {'type': 'object'}}}}}}
        if path == '/api/v1/connection/discover':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'type': 'object', 'required': ['models', 'fallback'], 'properties': {'models': {'type': 'array', 'items': {'type': 'object'}}, 'fallback': {'type': 'boolean'}}}}}
        if path == '/api/v1/sessions/activity' and method == 'GET':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/SessionActivity'}}}
        if path == '/api/v1/sessions' and method == 'GET':
            operation['responses']['200']['content'] = {'application/json': {'schema': {
                'type': 'object', 'required': ['sessions'], 'properties': {'sessions': {
                    'type': 'array', 'items': {'type': 'object', 'required': ['id'], 'properties': {
                        'id': {'type': 'string'}, 'title': {'type': ['string', 'null']},
                        'cwd': {'type': ['string', 'null']}, 'created_at': {'type': 'integer'},
                        'metadata': {'type': 'object'}, 'event_count': {'type': 'integer'},
                        'message_count': {'type': 'integer'}}}}}}}}
        if path == '/api/v1/sessions/{session_id}/requests/{trace_id}' and method == 'GET':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'anyOf': [
                {'$ref': '#/components/schemas/RequestTraceDetailView'}, {'$ref': '#/components/schemas/RequestTracePartView'}]}}}
            parameters.extend([
                {'name': 'part', 'in': 'query', 'schema': {'type': 'string', 'enum': ['summary', 'body', 'body_formatted', 'message', 'tools', 'tool']}},
                {'name': 'index', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 0}},
                {'name': 'offset', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 0}}])
        if path.endswith('/permissions'):
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/SessionPermissionResponse'}}}
            operation['responses']['404'] = {'description': tr('python.protocol.2d98325ea8eb')}
            if method == 'PATCH':
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/SetSessionPermission'}}}}
                operation['responses']['409'] = {'description': tr('python.protocol.f05fd33718bd'), 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ApiError'}}}}
        personality_response = None
        personality_request = None
        if path == '/api/v1/personalities':
            personality_response = 'PersonalityCatalog' if method == 'GET' else 'PersonalityProfile'
            if method == 'POST':
                personality_request = 'SavePersonalityProfile'
        elif path == '/api/v1/personalities/{profile_id}':
            personality_request = 'DeletePersonalityProfile'
            operation['responses']['200']['content'] = {'application/json': {'schema': {'type': 'object', 'required': ['deleted'], 'properties': {'deleted': {'type': 'boolean'}}}}}
        elif path == '/api/v1/personalities/recommend':
            personality_request = 'RecommendPersonality'
            operation['responses']['200']['content'] = {'application/json': {'schema': {'type': 'object', 'required': ['code'], 'properties': {'code': {'type': 'string', 'enum': list(STYLE_CODES)}}}}}
        elif path == '/api/v1/personality-selection':
            personality_response = 'PersonalitySelection'
            if method == 'PATCH':
                personality_request = 'SetPersonalitySelection'
        elif path == '/api/v1/sessions/{session_id}/personality':
            personality_response = 'SessionPersonality'
            operation['responses']['404'] = {'description': tr('python.protocol.2d98325ea8eb')}
            if method == 'PATCH':
                personality_request = 'SetSessionPersonality'
        if personality_response:
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/' + personality_response}}}
        if personality_request:
            operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/' + personality_request}}}}
            if personality_request in {'SavePersonalityProfile', 'DeletePersonalityProfile'}:
                operation['responses']['503'] = {'description': tr('protocol.personality_unavailable'), 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ApiError'}}}}
            if personality_request != 'RecommendPersonality':
                operation['responses']['409'] = {'description': tr('protocol.version_changed'), 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ApiError'}}}}
        if path.endswith('/permissions/grants'):
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/PermissionGrants'}}}
        if path.endswith('/permissions/grants/{grant_id}'):
            operation['responses']['404'] = {'description': tr('python.protocol.8b44bf6e6c0f')}
            if method == 'GET':
                operation['responses']['200']['content'] = {'application/json': {'schema': {'type': 'object', 'required': ['grant'], 'properties': {'grant': {'$ref': '#/components/schemas/PermissionGrant'}}}}}
            else:
                operation['responses']['200']['content'] = {'application/json': {'schema': {'type': 'object', 'required': ['revoked'], 'properties': {'revoked': {'type': 'string'}, 'audit_error': {'type': 'string'}}}}}
        if path.endswith('/queue') and method == 'GET':
            operation['responses']['200']['content'] = {'application/json': {'schema': {
                'type': 'object', 'required': ['runs'], 'properties': {'runs': {
                    'type': 'array', 'items': {'$ref': '#/components/schemas/QueueEntry'}}}}}}
        if path.endswith('/queue-control'):
            operation['responses']['200']['content'] = {"application/json": {"schema": {"$ref": "#/components/schemas/QueueControl"}}}
            if method == 'PATCH':
                operation['requestBody'] = {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SetQueuePaused"}}}}
        if path.endswith('/queue-order') and method == 'POST':
            operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {
                'type': 'object', 'required': ['kind', 'expected', 'order'], 'properties': {
                    'kind': {'type': 'string', 'enum': ['runs']},
                    'expected': {'type': 'array', 'items': {'type': 'object', 'required': ['id', 'edit_version'], 'properties': {
                        'id': {'type': 'string'}, 'edit_version': {'type': 'string', 'minLength': 64, 'maxLength': 64}}}},
                    'order': {'type': 'array', 'uniqueItems': True, 'items': {'type': 'string'}}}}}}}
            operation['responses']['200']['content'] = {'application/json': {'schema': {'type': 'object', 'required': ['reordered'], 'properties': {'reordered': {'type': 'boolean'}}}}}
            operation['responses']['409'] = {'description': tr('python.protocol.423ac8bfbb49'), 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ApiError'}}}}
        queue_mutation = method in {'PATCH', 'DELETE'} and ('/sessions/{session_id}/runs/{run_id}' == path.removeprefix('/api/v1'))
        if queue_mutation:
            response = {'$ref': '#/components/schemas/QueueEntry'} if method == 'PATCH' else {
                'type': 'object', 'required': ['removed'], 'properties': {'removed': {'type': 'string'}}}
            operation['responses']['200']['content'] = {'application/json': {'schema': response}}
            operation['responses']['409'] = {"description": tr('python.protocol.423ac8bfbb49'), "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}}
            if method == 'PATCH':
                operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/EditQueuedInput"}}}}
            else:
                parameters.append({"name": "expected_version", "in": "query", "required": True, "schema": {"type": "string", "minLength": 64, "maxLength": 64}})
        if method == "GET" and path.endswith("/conversation"):
            parameters.extend([
                {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100}},
                {"name": "cursor", "in": "query", "schema": {"type": "string", "maxLength": 2048}, "description": tr('python.protocol.cc53a9ee5d66')},
            ])
            operation["responses"]["200"]["content"] = {"application/json": {"schema": {"$ref": "#/components/schemas/ConversationPage"}}}
            operation["responses"]["409"] = {"description": tr('python.protocol.a4578b651257'), "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ApiError"}}}}
        if path == '/api/v1/ui-settings':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/UiSettingsResponse'}}}
            if method == 'PATCH':
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/UiSettingsPatch'}}}}
        if path == '/api/v1/settings':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/SettingsResponse'}}}
            if method == 'PATCH':
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/SettingsPatch'}}}}
        if path.endswith('/context/compact'):
            if method == 'POST':
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {
                    'type': 'object', 'required': ['request_id'], 'properties': {'request_id': {'type': 'string', 'minLength': 1, 'maxLength': 200}, 'restart': {'type': 'boolean', 'default': False}, 'mode': {'type': 'string', 'enum': ['compact', 'project'], 'default': 'compact'}}, 'additionalProperties': False}}}}
                operation['responses']['202'] = {'description': tr('protocol.accepted'), 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/RunRecord'}}}}
                operation['responses'].pop('200', None)
            else:
                parameters.append({'name': 'request_id', 'in': 'query', 'schema': {'type': 'string'}})
                operation['responses']['200']['content'] = {'application/json': {'schema': {'anyOf': [{'$ref': '#/components/schemas/RunRecord'}, {'type': 'null'}]}}}
        if method == "POST" and path.endswith("/runs"):
            parameters.append({"name": "Idempotency-Key", "in": "header", "schema": {"type": "string"}})
            operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SubmitRun"}}}}
            operation["responses"] = {"202": {"description": tr('python.protocol.75f245a0e64e'), "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RunRecord"}}}}, "400": operation["responses"]["400"]}
        elif method == "POST" and path.endswith("/steer"):
            queued = '/runs/{run_id}/steer' in path
            if not queued:
                parameters.append({'name': 'Idempotency-Key', 'in': 'header', 'schema': {'type': 'string'}})
            operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/" + ('SteerQueuedRun' if queued else 'SteerRun')}}}}
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/SteerAccepted'}}}
            if queued:
                operation['responses']['409'] = {'description': tr('python.protocol.5e39b9636ca2')}
        if method == 'GET' and path.endswith('/steering/{request_id}'):
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/SteeringStatus'}}}
        elif method == 'POST' and path.endswith('/continue'):
            parameters.append({"name": "Idempotency-Key", "in": "header", "schema": {"type": "string"}})
            operation['requestBody'] = {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ContinueRun"}}}}
            operation['responses'] = {'202': {"description": tr('python.protocol.95d907eec343'), "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RunRecord"}}}}, '400': operation['responses']['400'], '404': {"description": tr('python.protocol.85e67fe46e27')}}
        if path == '/api/v1/sessions/{session_id}/memory/preview' and method == 'GET':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/MemoryPreview'}}}
        elif '/sessions/{session_id}/memory' in path:
            parameters.append({'name': 'scope', 'in': 'query', 'schema': {
                'type': 'string', 'enum': ['user', 'project', 'session'], 'default': 'project'}})
        if method == 'GET' and path.endswith('/runs'):
            parameters.extend([
                {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 200, "default": 30}},
                {"name": "before", "in": "query", "schema": {"type": "string"}, "description": tr('python.protocol.72902866915c')},
                {"name": "include_queued", "in": "query", "schema": {"type": "string", "enum": ['0', '1'], "default": '1'}},
                {"name": "summary", "in": "query", "schema": {"type": "string", "enum": ['0', '1'], "default": '0'}, "description": tr('python.protocol.7933f12e8155')},
            ])
            operation['responses']['200']['content'] = {"application/json": {"schema": {"$ref": "#/components/schemas/RunPage"}}}
        if method == 'GET' and path.endswith('/runs/{run_id}'):
            operation['responses']['200']['content'] = {"application/json": {"schema": {"$ref": "#/components/schemas/RunRecord"}}}
            if '/sessions/' in path:
                operation['responses']['404'] = {"description": tr('python.protocol.365e5c3993c4')}
        if method == 'GET' and path.endswith('/turns/{turn}/completion'):
            parameters.append({'name': 'offset', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 0, 'default': 0}})
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/TurnCompletion'}}}
        if method == 'GET' and path.endswith('/turns/{turn}/run'):
            operation['responses']['200']['content'] = {"application/json": {"schema": {"$ref": "#/components/schemas/RunRecord"}}}
            operation['responses']['404'] = {"description": tr('python.protocol.8f55ed9bb00e')}
            operation['responses']['409'] = {"description": tr('python.protocol.d48840a45402')}
        if method == 'GET' and path.endswith('/runs/{run_id}/recovery'):
            parameters.extend([{"name": "offset", "in": "query", "schema": {"type": "integer", "minimum": 0, "default": 0}},
                               {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50}}])
            operation['responses']['200']['content'] = {"application/json": {"schema": {"$ref": "#/components/schemas/RecoveryPreview"}}}
        if method == 'GET' and (path.endswith('/comparison') or path.endswith('/comparison/options')):
            options = path.endswith('/options')
            schema = 'ComparisonOptions' if options else 'Comparison'
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/' + schema}}}
            names = ['search', 'offset'] if options else ['scope', 'base', 'target', 'revision', 'path', 'offset']
            parameters.extend([{'name': name, 'in': 'query', 'schema': {'type': 'integer' if name == 'offset' else 'string'}} for name in names])
            operation['responses']['409'] = {'description': tr('comparison.changed')}
        if method == 'GET' and '/turns/{turn}/changes' in path:
            schema = 'FileOperationPatch' if path.endswith('/{source_seq}') else 'TurnChanges'
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/' + schema}}}
            operation['responses']['404'] = {'description': tr('python.protocol.366007f417dd')}
            operation['responses']['409'] = {'description': tr('python.protocol.d48840a45402')}
            if schema == 'TurnChanges':
                parameters.extend([{'name': 'offset', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 0, 'default': 0}},
                                   {'name': 'through', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 0}, 'description': tr('python.protocol.5cfe8e33bdf5')}])
        if method == 'GET' and (path.endswith('/turns/{turn}/net-changes') or path.endswith('/turns/{turn}/net-changes/{source_seq}')):
            detail = path.endswith('/{source_seq}')
            schema = 'NetFilePatch' if detail else 'TurnNetChanges'
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/' + schema}}}
            operation['responses']['404'] = {'description': tr('python.protocol.e3e7d828fc05')}
            operation['responses']['409'] = {'description': tr('python.protocol.d48840a45402')}
            parameters.append({'name': 'through', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 0}, 'description': tr('python.protocol.a3b999dca9cc')})
            if not detail:
                parameters.append({'name': 'offset', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 0, 'default': 0}})
        if '/net-changes/{source_seq}/restore' in path:
            schema = 'FileRestorePreview' if method == 'GET' and path.endswith('/restore') else 'FileRestoreReceipt'
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/' + schema}}}
            operation['responses']['404'] = {'description': tr('python.protocol.d13ea5f829f3')}
            operation['responses']['409'] = {'description': tr('python.protocol.3ab7ed7aa5eb')}
            if method == 'GET' and path.endswith('/restore'):
                parameters.append({'name': 'through', 'in': 'query', 'required': True, 'schema': {'type': 'integer', 'minimum': 0}})
            if method == 'POST':
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/FileRestoreRequest'}}}}
        if method == "GET" and path.endswith("/changes") and '/turns/' not in path:
            parameters.append({"name": "path", "in": "query", "schema": {"type": "string"}, "description": tr('python.protocol.2ab13b54456a')})
            operation["responses"]["200"]["content"] = {"application/json": {"schema": {"oneOf": [{"$ref": "#/components/schemas/ReviewSummary"}, {"$ref": "#/components/schemas/ReviewPatch"}]}}}
        if method == "GET" and path.endswith("/submissions/{key}"):
            operation["responses"]["200"]["content"] = {"application/json": {"schema": {"$ref": "#/components/schemas/SubmissionStatus"}}}
        if method == "POST" and path == "/api/v1/questions/answer":
            operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SubmitAnswers"}}}}
        if '/subagents/{agent_id}' in path:
            operation['description'] = tr('python.protocol.d97223f54d02')
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/SubagentDetailResponse'}}}
            operation['responses']['404'] = {'description': tr('python.protocol.a99fd28bf21f')}
        if path.endswith('/subagents'):
            operation['description'] = tr('python.protocol.2da8efd3913d')
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/SubagentPage'}}}
            parameters.extend([{'name': 'before', 'in': 'query', 'schema': {'type': 'string'}}, {'name': 'limit', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 50}}])
        if path.endswith('/jobs'):
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/JobHistory'}}}
            parameters.extend([{'name': 'before', 'in': 'query', 'schema': {'type': 'string'}}, {'name': 'limit', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 50}}])
            operation['responses']['404'] = {'description': tr('python.protocol.0178f467df02')}
            operation['responses']['503'] = {'description': tr('python.protocol.6b863d37b130')}
        if "/jobs/{job_id}/" in path:
            schema = "JobOutput" if path.endswith("/output") else "JobStop"
            operation["responses"]["200"]["content"] = {"application/json": {"schema": {"$ref": "#/components/schemas/" + schema}}}
            operation["responses"]["404"] = {"description": tr('python.protocol.6974695774e4')}
            if path.endswith("/output"):
                operation['responses']['503'] = {'description': tr('python.protocol.77f8a00ffac8')}
                parameters.append({"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 32768, "default": 32768}, "description": tr('python.protocol.617cfe82e3bc')})
        if path.startswith('/api/v1/terminal/setup'):
            schema = 'TerminalRuntimeSetup' if path.endswith('/setup') else 'TerminalRuntimeResult'
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/' + schema}}}
            if path.endswith('/install'):
                if method == 'GET':
                    parameters.append({'name': 'id', 'in': 'query', 'schema': {'type': 'string', 'format': 'uuid'}})
                else:
                    request_schema = 'TerminalRuntimeInstall' if method == 'POST' else 'TerminalRuntimeCancel'
                    operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/' + request_schema}}}}
                operation['responses']['404'] = {'description': tr('python.terminal_setup.missing')}
                operation['responses']['409'] = {'description': tr('python.terminal_setup.busy')}
        if method == 'GET' and path in QUERY_SCHEMAS:
            parameters.extend(query_parameters(QUERY_SCHEMAS[path]))
            operation['description'] = tr('web.session_list_query' if path == '/api/v1/sessions' else
                'web.session_activity_query' if path.endswith('/activity') else
                'web.live_events_query' if path.endswith('/live-events') else 'web.runtime_query')
            operation['responses']['400'] = {'description': tr('web.query_invalid'), 'content': {
                'application/json': {'schema': {'$ref': '#/components/schemas/ApiError'}}}}
        if "/terminals/{terminal_id}/" in path:
            action = path.rsplit('/', 1)[-1]
            schema = {'output': 'TerminalOutput', 'stop': 'TerminalStop', 'stream': 'TerminalStream', 'input': 'TerminalControl', 'resize': 'TerminalControl'}[action]
            operation["responses"]["200"]["content"] = {"application/json": {"schema": {"$ref": "#/components/schemas/" + schema}}}
            operation["responses"]["404"] = {"description": tr('python.protocol.cda5311aecd8')}
            if path.endswith('/output') and path not in QUERY_SCHEMAS:
                parameters.append({"name": "count", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 500}, "description": tr('python.protocol.b065dee02681')})
            if action == 'stream':
                parameters.extend([{'name': 'cursor', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 0, 'maximum': 2**53 - 1, 'default': 0}},
                                   {'name': 'wait_ms', 'in': 'query', 'schema': {'type': 'integer', 'minimum': 0, 'maximum': 1000, 'default': 1000}}])
                operation['description'] = tr('python.protocol.1b75aefd943c')
            if action in {'input', 'resize'}:
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/' + ('TerminalInput' if action == 'input' else 'TerminalResize')}}}}
                operation['responses']['409'] = {'description': tr('python.protocol.12ef38b84e8b')}
        if path == '/api/v1/voice/preparations':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/VoicePreparation'}}}
        if path == '/api/v1/voice/transcriptions':
            operation['requestBody'] = {'required': True, 'content': {'audio/wav': {'schema': {'type': 'string', 'format': 'binary'}}}}
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/VoiceTranscript'}}}
        if path in {'/api/v1/voice/dictation', '/api/v1/voice/dictation/open'}:
            schema = 'VoiceDictationCapabilities' if method == 'GET' else 'VoiceDictationOpen'
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/' + schema}}}
        if path == '/api/v1/screenshot/capabilities':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/ScreenshotCapabilities'}}}
        if path == '/api/v1/terminal/types':
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/TerminalTypes'}}}
        if path.endswith('/terminals'):
            operation['responses']['200']['content'] = {'application/json': {'schema': {'$ref': '#/components/schemas/' + ('TerminalList' if method == 'GET' else 'TerminalControl')}}}
            if method == 'POST':
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/TerminalOpen'}}}}
                operation['description'] = tr('python.protocol.fb04afd0b279')
                operation['responses']['409'] = {'description': tr('python.protocol.37a1140c3d86')}
        if path.startswith('/api/v1/automations') or path.startswith('/api/v1/automation-runs'):
            operation['description'] = tr('python.protocol.87993599c199')
            if method == 'POST' and path in {'/api/v1/automations', '/api/v1/automations/preview'}:
                schedule = {'oneOf': [
                    {'type': 'object', 'required': ['kind', 'at'], 'properties': {'kind': {'const': 'once'}, 'at': {'type': 'number', 'exclusiveMinimum': 0}}},
                    {'type': 'object', 'required': ['kind', 'seconds'], 'properties': {'kind': {'const': 'interval'}, 'seconds': {'type': 'number', 'minimum': 60, 'maximum': 31536000}}},
                    {'type': 'object', 'required': ['kind', 'time'], 'properties': {'kind': {'const': 'daily'}, 'time': {'type': 'string', 'pattern': '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$'}}},
                    {'type': 'object', 'required': ['kind', 'time', 'days'], 'properties': {'kind': {'const': 'weekly'}, 'time': {'type': 'string', 'pattern': '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$'}, 'days': {'type': 'array', 'minItems': 1, 'items': {'type': 'integer', 'minimum': 0, 'maximum': 6}}}},
                    {'type': 'object', 'required': ['kind', 'time', 'monthdays'], 'properties': {'kind': {'const': 'monthly'}, 'time': {'type': 'string', 'pattern': '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$'}, 'monthdays': {'type': 'array', 'minItems': 1, 'items': {'type': 'integer', 'minimum': 1, 'maximum': 31}}}},
                    {'type': 'object', 'required': ['kind', 'time', 'months', 'monthdays'], 'properties': {'kind': {'const': 'yearly'}, 'time': {'type': 'string', 'pattern': '^(?:[01][0-9]|2[0-3]):[0-5][0-9]$'}, 'months': {'type': 'array', 'minItems': 1, 'items': {'type': 'integer', 'minimum': 1, 'maximum': 12}}, 'monthdays': {'type': 'array', 'minItems': 1, 'items': {'type': 'integer', 'minimum': 1, 'maximum': 31}}}},
                ]}
                properties = {'schedule': schedule, 'timezone': {'type': 'string', 'default': 'Asia/Shanghai'}}
                required = ['schedule']
                if path == '/api/v1/automations':
                    required += ['name', 'prompt', 'session_id']
                    properties.update({'id': {'type': 'string', 'minLength': 1}, 'creation_id': {'type': 'string', 'pattern': '^[0-9a-f]{32}$'}, 'name': {'type': 'string', 'minLength': 1, 'maxLength': 120},
                        'prompt': {'type': 'string', 'minLength': 1, 'maxLength': 32000}, 'session_id': {'type': 'string'},
                        'mode': {'type': 'string', 'enum': ['session', 'fresh'], 'default': 'session'}, 'enabled': {'type': ['boolean', 'integer']}})
                    operation['responses']['201'] = operation['responses'].pop('200')
                else:
                    operation['responses']['200']['content'] = {'application/json': {'schema': {'type': 'object', 'required': ['occurrences', 'timezone'], 'properties': {'occurrences': {'type': 'array', 'items': {'type': 'number'}}, 'timezone': {'type': 'string'}}}}}
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {'type': 'object', 'required': required, 'properties': properties}}}}
                if path == '/api/v1/automations':
                    operation['requestBody']['content']['application/json']['schema']['oneOf'] = [
                        {'required': ['id'], 'not': {'required': ['creation_id']}},
                        {'required': ['creation_id'], 'not': {'required': ['id']}},
                    ]
            if method == 'POST' and path.endswith('/run'):
                operation['responses']['202'] = {'description': tr('python.protocol.3ccaa7853c11'), 'content': {'application/json': {'schema': {'type': 'object', 'required': ['run_id'], 'properties': {'run_id': {'type': 'string'}}}}}}
                operation['responses'].pop('200')
        if method == 'POST' and path == '/api/v1/questions/answer':
            operation['responses']['409'] = {'description': tr('python.audit.question_unavailable'),
                'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ApiError'}}}}
        # 公共 HTTP 边界可在业务处理前拒绝请求，也可能返回结果未知的等待超时。
        operation['responses']['default'] = {'description': tr('python.audit.http_error'),
            'content': {'application/json': {'schema': {'$ref': '#/components/schemas/ApiError'}}}}
        if (method,path) in SUPERAGENT_ENDPOINTS:
            input_name,response_name = SUPERAGENT_ENDPOINTS[(method,path)]
            operation['responses']['200']['content'] = {'application/json':{'schema':{'$ref':'#/components/schemas/'+response_name}}}
            if input_name:
                operation['requestBody'] = {'required':True,'content':{'application/json':{'schema':{'$ref':'#/components/schemas/'+input_name}}}}
        if path.startswith('/api/v1/superagents/') and path.endswith('/media/{media_id}'):
            operation['responses']['200']['content'] = {mime:{'schema':{'type':'string','format':'binary'}}
                for mime in ('application/octet-stream','image/png','image/jpeg','image/webp','image/gif')}
        if (method, path) in ACADEMY_ENDPOINTS:
            input_name, response = ACADEMY_ENDPOINTS[(method, path)]
            operation['responses']['200']['content'] = {'application/json': {'schema': response}}
            if input_name:
                operation['requestBody'] = {'required': True, 'content': {'application/json': {'schema': {
                    '$ref': '#/components/schemas/' + input_name}}}}
            if path.endswith('/source') or path.endswith('/reference'):
                parameters.append({'name': 'path', 'in': 'query', 'required': True, 'schema': {'type': 'string'}})
            if method == 'GET' and path.endswith('/restore'):
                parameters.append({'name': 'checkpoint_id', 'in': 'query', 'required': True, 'schema': {'type': 'string'}})
        body = request_body(method, path)
        if body is not None and 'requestBody' not in operation:
            operation['requestBody'] = body
        operation["parameters"] = parameters
        paths.setdefault(path, {})[method.lower()] = operation
    return {"openapi": "3.1.0", "info": {"title": tr("protocol.title"), "version": "1"}, "paths": paths, "components": {"schemas": _localized_schemas()}}


def typescript() -> str:
    def ts_type(schema):
        if "const" in schema:
            return json.dumps(schema["const"])
        for union in ('oneOf', 'anyOf'):
            if union in schema:
                return " | ".join(ts_type(part) for part in schema[union])
        if "enum" in schema:
            return " | ".join(repr(value) for value in schema["enum"])
        if "$ref" in schema:
            return schema["$ref"].split("/")[-1]
        kind = schema.get("type")
        if kind == "array":
            return "Array<" + ts_type(schema["items"]) + ">"
        if kind == "object" and isinstance(schema.get("additionalProperties"), dict):
            return "Record<string, " + ts_type(schema["additionalProperties"]) + ">"
        if isinstance(kind, list):
            return " | ".join(ts_type({**schema, "type": part}) for part in kind)
        return {"string": "string", "integer": "number", "number": "number", "boolean": "boolean", "object": "Record<string, unknown>", "null": "null"}.get(kind, "unknown")
    lines = ["// Generated by scripts/generate_protocol.py. Do not edit."]
    for name, schema in SCHEMAS.items():
        if schema.get('type') != 'object':
            lines.append(f'export type {name} = {ts_type(schema)}')
            continue
        lines.append(f"export interface {name} {{")
        for key, value in schema["properties"].items():
            optional = "" if key in schema.get("required", []) else "?"
            lines.append(f"  {key}{optional}: {ts_type(value)}")
        lines.append("}")
    lines.append('export const projectTaskStates = ' + json.dumps(STATES) + ' as const')
    lines.append("export const projectTaskTransitions: Record<ProjectTask['status'], ProjectTask['status'][]> = "
                 + json.dumps({state: [target for target in STATES if target in TRANSITIONS[state]] for state in STATES}))
    return "\n".join(lines) + "\n"
