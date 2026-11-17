import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it, vi} from 'vitest'
import {setLocale} from '@/i18n'

const hooks = {supported: true}
vi.mock('@/hooks/useVoiceInputSupported', () => ({useVoiceInputSupported: () => hooks.supported}))
import {AcademyVoiceButton} from '@/components/inspector/AcademyVoice'

afterEach(async () => {await setLocale('zh-CN'); hooks.supported = true})

it('mounts the mic only when voice input is supported', async () => {
  await setLocale('en')
  hooks.supported = true
  const on = renderToStaticMarkup(createElement(AcademyVoiceButton, {active: true, onText: () => {}}))
  expect(on).toContain('data-academy-voice')
  hooks.supported = false
  const off = renderToStaticMarkup(createElement(AcademyVoiceButton, {active: true, onText: () => {}}))
  expect(off).toBe('')
})
