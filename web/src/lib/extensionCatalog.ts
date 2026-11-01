import type { AgentMountState } from '@/lib/agentMount'

export interface SkillRow {
  interface?: {display_name?: string; short_description?: string; default_prompt?: string}
  configuration_errors?: string[]
  plugin_id?: string
  plugin_enabled?: boolean
  name: string
  description: string
  when_to_use?: string
  path?: string | null
  source?: string
  provider?: string
  model_invocable?: boolean
  user_invocable?: boolean
  removal_scope?: 'project' | 'user' | null
  enabled: boolean
}

export interface AgentMember {
  display_name?: string | null
  runner?: 'codex' | 'claude' | 'opencode' | null
  name: string
  role: string
  agent?: string | null
  prompt?: string
  model?: string | null
}

export interface AgentRow {
  runner?: 'codex' | 'claude' | 'opencode' | null
  edit_scope?: 'project' | 'user' | null
  edit_revision?: string
  system_prompt?: string
  metadata?: Record<string, unknown>
  name: string
  display_name?: string
  profession?: string
  description: string
  agent_type: 'agent' | 'team'
  model?: string | null
  members?: AgentMember[]
  path?: string | null
  source?: string
  /** 头像接口路径（相对 /api/v1/plugins），无头像时为 null。 */
  avatar?: string | null
  enabled: boolean
}

export interface ConnectorRow {
  name: string
  transport: string
  command?: string | null
  args?: string[]
  url?: string | null
  enabled: boolean
  bound: boolean
  connected: boolean
  tool_count: number
  last_error?: string
  metadata?: {
    description?: string
    homepage?: string
    auth_hint?: string
    preset?: string
    plugin_id?: string
    plugin_enabled?: boolean
    display_name?: string
  }
  oauth?: {
    supported: boolean
    state: 'none' | 'pending' | 'authorized' | 'error'
    backend?: string
    mode?: string
    user_code?: string | null
    verification_uri?: string | null
    authorize_url?: string | null
    started_at?: number
    scope?: string
    expires_at?: number | null
    message?: string
  }
}

/** 精选连接器预设（对齐 GET /api/v1/plugins/connectors/presets） */
export interface PresetRow {
  name: string
  transport: string
  url?: string | null
  description: string
  homepage: string
  auth_hint: string
  has_skill: boolean
  installed: boolean
}

export interface Overview {
  capabilities?: { persistent_team_selection?: boolean }
  skills: SkillRow[]
  environment?: Array<{ server_name: string; transport: string }>
  agents: AgentRow[]
  connectors: ConnectorRow[]
  enabled: { skills: boolean; agents: boolean; connectors: boolean }
}

export interface MountState extends AgentMountState {
  display_name?: string
  available: boolean
  agent: string | null
  model?: string | null
  description?: string
  agent_type?: 'agent' | 'team'
}
