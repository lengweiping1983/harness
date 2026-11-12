import { describe, expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import { HOTKEY_ROWS } from '../hotkeyCatalog'

describe('shortcut terminology', () => {
  it('resolves the same shortcut row across repeated language switches', () => {
    const row = HOTKEY_ROWS[0]
    for (const [locale, group, label] of [['en', 'Conversation', 'New conversation'], ['zh-CN', '对话', '新建对话'], ['en', 'Conversation', 'New conversation']] as const) {
      setLocale(locale)
      expect(row.group).toBe(group)
      expect(row.label).toBe(label)
    }
  })
})
