import type { ContextManagementSettings } from './protocol.generated'

const profiles = [
  { id: 'efficient', ratio: .4, fallback: 128000, tool_result_max_chars: 8000, summary_max_tokens: 4096 },
  { id: 'balanced', ratio: .6, fallback: 256000, tool_result_max_chars: 12000, summary_max_tokens: 8192 },
  { id: 'extended', ratio: .8, fallback: 384000, tool_result_max_chars: 24000, summary_max_tokens: 8192 },
] as const
export type ContextPresetId = typeof profiles[number]['id']

export function contextWindow(model: number | null | undefined, limit?: number | null): number | null {
  const values = [model, limit].filter((value): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value > 0)
  return values.length ? Math.min(...values) : null
}

export function contextPresets(window: number | null, outputReserve = 16000) {
  const safeBudget = window === null ? null : Math.max(1, Math.floor(window * .85) - Math.min(outputReserve, Math.floor(window / 4)))
  return profiles.map(profile => {
    const target = window === null ? profile.fallback : Math.max(1, Math.floor(window * profile.ratio))
    const budget = safeBudget === null ? target : Math.min(target, safeBudget)
    return {...profile, context_working_budget: budget, limited: budget < target}
  })
}

export function applyContextPreset(policy: ContextManagementSettings, id: ContextPresetId, window: number | null): ContextManagementSettings {
  const preset = contextPresets(window, policy.output_reserve).find(item => item.id === id)!
  return { ...policy, context_working_budget: preset.context_working_budget,
    tool_result_max_chars: preset.tool_result_max_chars, summary_max_tokens: preset.summary_max_tokens,
    context_auto_manage: true, tool_schema_mode: 'auto' }
}

/** 非标准预算先预选均衡；只改变草稿，保存前不发布配置。 */
export function selectedContextPreset(policy: ContextManagementSettings, window: number | null): ContextPresetId {
  const candidates = contextPresets(window, policy.output_reserve).filter(item => item.context_working_budget === policy.context_working_budget)
  return candidates.find(item => item.tool_result_max_chars === policy.tool_result_max_chars && item.summary_max_tokens === policy.summary_max_tokens)?.id
    ?? candidates.find(item => item.id === 'balanced')?.id ?? candidates[0]?.id ?? 'balanced'
}
