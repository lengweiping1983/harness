import {afterEach, expect, test} from 'vitest'
import {setLocale, t} from '@/i18n'
import {modelFailure} from '../modelFailure'
import {noticeText} from '../noticeText'

afterEach(() => setLocale('zh-CN'))

test('local profile failures use actionable guidance in the current language', () => {
  const copy = {key: 'ui.llm.opencodeLocalProfileExecutableChanged', params: {
    executable: '/Applications/OpenCode.app/Contents/Resources/opencode-cli',
    profile: '/fixture/profile.json',
  }}
  const translations: string[] = []
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    expect(modelFailure('original diagnostic', 'llm/opencode-local-profile')).toEqual({
      title: t('ui.modelFailure.localProfileRequired'), hint: t('ui.modelFailure.localProfileHint'),
    })
    const message = noticeText(copy)
    expect(message).toBe(t(copy.key, copy.params))
    expect(message).toContain(copy.params.executable)
    expect(message).toContain(copy.params.profile)
    expect(message).toContain('--refresh-backup')
    translations.push(message)
  }
  expect(translations[0]).toBe(translations[2])
  expect(translations[0]).not.toBe(translations[1])
})

test('network failures retain their separate connection guidance', () => {
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    expect(modelFailure('original diagnostic', 'llm/opencode-local-runtime')).toEqual({
      title: t('ui.modelFailure.requestIncomplete'), hint: t('ui.modelFailure.requestHint'),
    })
  }
})
