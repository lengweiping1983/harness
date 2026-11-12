import { expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import { todoFailure } from '../todoFailure'
import type { ToolBlockState } from '@/types'

const block = (display: unknown): ToolBlockState => ({ kind: 'tool', id: 't', callId: 't', name: 'todo_update', status: 'error', at: 1, display })
it('explains rejected updates in the current language without trusting historical messages', () => {
  const receipt = block({ code: 'VERSION_REQUIRED', updated: false, message: 'stale language' })
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const result = todoFailure(receipt)!
    expect(result.reason).toContain('snapshot_token')
    expect(result.reason).not.toContain('stale language')
    expect(result.recovery).toContain('todo_write')
  }
})
it('preserves unknown, unconfirmed and unrelated tool errors', () => {
  expect(todoFailure(block({ code: 'VERSION_REQUIRED' }))).toBeNull()
  expect(todoFailure(block({ code: 'FUTURE', updated: false }))).toBeNull()
  expect(todoFailure({ ...block({ code: 'VERSION_REQUIRED', updated: false }), name: 'bash' })).toBeNull()
  expect(todoFailure({ ...block(null), output: '{bad' })).toBeNull()
})
