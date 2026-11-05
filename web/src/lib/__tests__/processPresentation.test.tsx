import {afterEach, expect, it} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {ThinkingBlock} from '@/components/chat/blocks/MessageBlocks'
import {ToolBlock} from '@/components/chat/blocks/ToolBlock'
import {setLocale, t} from '@/i18n'
import type {ToolBlockState} from '@/types'

afterEach(() => setLocale('zh-CN'))

it('紧凑思考摘要保留折叠语义、截断和流式提示，原文不进入摘要', () => {
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    for (const streaming of [false, true]) {
      const original = 'Original thought 原文😀'
      const html = renderToStaticMarkup(<ThinkingBlock block={{kind:'thinking', id:'compact-thought', at:1, text:original, partial:true, streaming}} />)
      const summary = html.match(/<summary\b[^>]*>([\s\S]*?)<\/summary>/)?.[1]
      expect(summary).toContain(t('ui.components.chat.blocks.MessageBlocks.a62acb184f47'))
      expect(summary).toContain(t('ui.components.chat.blocks.MessageBlocks.9f76fd20a5ba'))
      expect(summary?.includes(t('ui.components.chat.blocks.MessageBlocks.dc9591e56d50'))).toBe(streaming)
      expect(summary).not.toContain(original)
      expect(html).toContain(original)
      expect(html).not.toMatch(/<details\b[^>]*\bopen=/)
    }
  }
})

it('紧凑工具摘要保留原命令、失败状态和独立详情关联', () => {
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    for (const failed of [false, true]) {
      const block: ToolBlockState = {kind:'tool', id:'compact-command', callId:'compact-call', at:1, name:'bash', status:failed ? 'error' : 'ok', input:{command:'python3 verify.py'}, display:{exit_code:failed ? 2 : 0}, output:'Original output 原文😀'}
      const collapsed = renderToStaticMarkup(<ToolBlock block={block} sessionId="compact-owner" />)
      const summary = collapsed.match(/<button\b[^>]*>([\s\S]*?)<\/button>/)?.[1]
      expect(summary).toContain('python3 verify.py')
      expect(summary?.includes('lucide-triangle-alert')).toBe(failed)
      expect(collapsed).toContain('aria-expanded="false"')
      expect(collapsed).not.toContain(block.output)
      const expanded = renderToStaticMarkup(<ToolBlock block={block} sessionId="compact-owner" defaultOpen />)
      const detailId = expanded.match(/aria-controls="([^"]+)"/)?.[1]
      expect(detailId).toBeTruthy()
      expect(expanded).toContain(`id="${detailId}"`)
      expect(expanded).toContain('aria-expanded="true"')
      expect(expanded).toContain(block.output)
    }
  }
})
