import {afterEach, expect, test} from 'vitest'
import {setLocale, t, hasTranslation} from '@/i18n'

afterEach(() => setLocale('zh-CN'))

test('translation parameters use own properties and preserve original content', () => {
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const key = 'ui.goal.retryError'
    const inherited = Object.create({error: 'inherited value'}) as Record<string, unknown>
    expect(t(key, inherited)).toBe(t(key))
    expect(t(key, inherited)).toContain('{error}')
    inherited.error = 'Original 原文 {unchanged}'
    expect(t(key, inherited)).toContain('Original 原文 {unchanged}')
    const direct = Object.assign(Object.create(null), {error: 0})
    expect(t(key, direct)).toContain('0')
    expect(t(key, direct)).not.toContain('{error}')
  }
})

for(const key of ['constructor','toString','__proto__']){
 test('prototype key '+key+' is missing from every resource lookup',()=>{
  for(const language of ['en','zh-CN','en'] as const){
   setLocale(language)
   expect(hasTranslation(key)).toBe(false)
   expect(()=>t(key)).toThrow('Missing '+language+' resource: '+key)
   expect(()=>t(key,{value:'Original 原文'})).toThrow('Missing '+language+' resource: '+key)
  }
 })
}
