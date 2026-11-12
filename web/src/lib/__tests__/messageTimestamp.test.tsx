import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it} from 'vitest'
import {setLocale, t} from '@/i18n'
import {MessageTimestamp} from '@/components/chat/blocks/MessageTimestamp'

afterEach(() => setLocale('zh-CN'))

it('消息和轮次直接显示跨天、跨年的日期，保留事件含义及原始时间', () => {
  const dates = [new Date(2025, 11, 31, 23, 59, 59), new Date(2026, 0, 1, 0, 0, 1)]
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    for (const kind of ['sent', 'completed'] as const) {
      for (const date of dates) {
        const html = renderToStaticMarkup(<MessageTimestamp at={date.getTime()} kind={kind} />)
        const visible = html.replace(/<[^>]*>/g, '')
        expect(visible).toContain(String(date.getFullYear()))
        expect(visible).toContain(date.toLocaleDateString(locale, {year:'numeric', month:'2-digit', day:'2-digit'}))
        expect(visible).toContain(date.toLocaleTimeString(locale, {hour12:false}))
        expect(html).toContain(`dateTime="${date.toISOString()}"`)
        expect(html).toContain(t(kind === 'sent' ? 'ui.messageTime.sent' : 'ui.messageTime.completed', {time:date.toLocaleString(locale)}))
      }
    }
  }
})

it('缺少有效时间时不补造日期', () => {
  for (const at of [0, NaN, Infinity]) expect(renderToStaticMarkup(<MessageTimestamp at={at} kind="completed" />)).toBe('')
})
