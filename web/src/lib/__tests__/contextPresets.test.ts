import {expect, it} from 'vitest'
import {applyContextPreset, contextPresets, contextWindow, selectedContextPreset} from '../contextPresets'
import type {ContextManagementSettings} from '../protocol.generated'
const policy: ContextManagementSettings = {context_auto_manage: true, context_working_budget: 384000, tool_result_max_chars: null, tool_schema_mode: 'auto', summary_max_tokens: null, output_reserve: 16000, context_window: null}
it('uses 40/60/80 percent targets capped by the runtime output reserve and safety threshold', () => {
  for (const window of [1, 4096, 128000, 1000000]) {
    const budgets = contextPresets(window).map(preset => preset.context_working_budget)
    const safe = Math.max(1, Math.floor(window * .85) - Math.min(16000, Math.floor(window / 4)))
    expect(budgets).toEqual([.4, .6, .8].map(ratio => Math.min(safe, Math.max(1, Math.floor(window * ratio)))))
    expect(budgets.every(budget => budget <= window)).toBe(true)
  }
})
it('uses the requested fallback budgets when capacity is unknown', () => {
  expect(contextPresets(null).map(preset => preset.context_working_budget)).toEqual([128000, 256000, 384000])
})
it('respects a lower configured context limit and does not invent capacity from invalid values', () => {
  expect(contextWindow(1000000, 128000)).toBe(128000)
  expect(contextWindow(null, 128000)).toBe(128000)
  expect(contextWindow(undefined)).toBeNull()
  for (const value of [0, -1, 1.5, Infinity, NaN]) expect(contextWindow(value)).toBeNull()
})
it('always chooses one budget profile independently of automatic management and advanced options', () => {
  expect(selectedContextPreset({...policy, context_working_budget: 380000}, null)).toBe('balanced')
  expect(selectedContextPreset({...policy, context_working_budget: 128000, context_auto_manage: false, tool_schema_mode: 'all'}, null)).toBe('efficient')
})
it('applies the current model budget without changing capacity limits or mutating the original policy', () => {
  const next = applyContextPreset(policy, 'extended', 128000)
  expect(next.context_working_budget).toBe(92800)
  expect(next.context_window).toBeNull()
  expect(next.output_reserve).toBe(16000)
  expect(policy.context_working_budget).toBe(384000)
})

it('retains profile identity when two targets meet the same safety cap', () => {
  const selected = applyContextPreset({...policy, output_reserve: 16000}, 'extended', 64000)
  expect(selected.context_working_budget).toBe(38400)
  expect(selectedContextPreset(selected, 64000)).toBe('extended')
  expect(contextPresets(1000000).map(preset => preset.context_working_budget)).toEqual([400000, 600000, 800000])
})
