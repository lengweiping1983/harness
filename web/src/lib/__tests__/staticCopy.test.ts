import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import { permissionProfiles } from '../permissions'
import { COMMANDS } from '../triggerSources'
import { GROUP_MODES } from '../sidebarGroups'
import { RUN_STATUS } from '../runHistory'
import { browserActionLabel } from '../browser'
import { COMPACTION_STATUS, CONTEXT_ROLE } from '../contextAudit'
import { REQUEST_ROLE, REQUEST_STATUS, REQUEST_TERMINAL_REASON } from '../requestTraces'
import { hookPhases, hookStates } from '../hookActivity'
import { turnOutcomeCopy } from '../turnOutcome'

afterEach(() => setLocale('zh-CN'))

describe('static product copy uses resource identity', () => {
  it('keeps held option and outcome objects live across English, Chinese and English', () => {
    const profile = permissionProfiles[0]
    const goal = COMMANDS.find(item => item.id === 'mode.goal')!
    const outcome = turnOutcomeCopy('blocked')!
    const group = GROUP_MODES[0]
    for (const locale of ['en', 'zh-CN', 'en'] as const) {
      setLocale(locale)
      expect(profile).toBe(permissionProfiles[0])
      expect(profile.id).toBe('request_approval')
      expect(profile.icon).toBe('hand')
      expect(profile.label).toBe(locale === 'en' ? 'Request approval' : '请求批准')
      expect(goal).toBe(COMMANDS.find(item => item.id === 'mode.goal'))
      expect(goal.insert).toBe('/goal')
      expect(goal.title).toBe('/goal')
      expect(goal.hint).toBe(locale === 'en'
        ? 'Select goal mode; send /goal to open setup when no goal exists'
        : '选择目标模式；无目标时发送 /goal 打开设置')
      expect(outcome).toBe(turnOutcomeCopy('blocked'))
      expect(outcome.title).toBe(locale === 'en' ? 'Task blocked' : '任务已阻断')
      expect(group.id).toBe('workspace')
      const labels = [profile.label, profile.description, outcome.title, outcome.hint, group.label,
        ...Object.values(RUN_STATUS), ...Object.values(browserActionLabel),
        ...Object.values(COMPACTION_STATUS), ...Object.values(CONTEXT_ROLE),
        ...Object.values(REQUEST_ROLE), ...Object.values(REQUEST_STATUS),
        ...Object.values(REQUEST_TERMINAL_REASON), ...Object.values(hookPhases),
        ...Object.values(hookStates), ...COMMANDS.map(item => item.hint!)]
      for (const label of labels) {
        expect(typeof label).toBe('string')
        expect(/\p{Script=Han}/u.test(label), label).toBe(locale === 'zh-CN')
      }
    }
  })
})
