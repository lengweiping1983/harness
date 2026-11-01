import { afterEach, describe, expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import { permissionDecision } from '../permissions'
import { scheduleLabel } from '../automations'

afterEach(() => setLocale('zh-CN'))
describe('composed product messages', () => {
  it('uses English separators and preserves supplied reasons', () => {
    setLocale('en')
    const result = permissionDecision(['allow', 'deny'], 'auto_review', 'user reason')
    expect(result).toContain(', ')
    expect(result).toContain('; user reason')
    expect(result).not.toMatch(/[、；]/)
    expect(scheduleLabel({ kind: 'weekly', days: [0, 2], time: '09:00' })).not.toContain('、')
  })
  it('uses Chinese punctuation without translating user content', () => {
    setLocale('zh-CN')
    const result = permissionDecision(['allow', 'deny'], 'auto_review', 'original reason')
    expect(result).toContain('、')
    expect(result).toContain('；original reason')
  })
})

it.each(['constructor','toString','__proto__'])('unknown decision %s stays original rather than resolving Object.prototype',value=>{
 for(const locale of ['en','zh-CN'] as const){setLocale(locale);expect(permissionDecision([value])).toBe(value)}
})
