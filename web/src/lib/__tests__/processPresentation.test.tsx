import {afterEach, expect, it} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {ThinkingBlock} from '@/components/chat/blocks/MessageBlocks'
import {ToolBlock} from '@/components/chat/blocks/ToolBlock'
import {setLocale, t} from '@/i18n'
import type {ToolBlockState} from '@/types'
import postcss from 'postcss'
import tailwindcss from 'tailwindcss'

afterEach(() => setLocale('zh-CN'))

it('新增修饰样式尚未加载时，工具摘要仍具备完整横向布局', async () => {
  const html = renderToStaticMarkup(<ToolBlock block={{kind:'tool', id:'layout-command', callId:'layout-call', at:1, name:'bash', status:'ok', input:{command:'python3 verify.py'}}} />)
  const classes = new Set(html.match(/<button\b[^>]*class="([^"]+)"/)![1].split(/\s+/))
  // 只编译实际组件的基础工具类，模拟热更新期间新增修饰样式尚未到达。
  const css = await postcss([tailwindcss({content:[{raw:html, extension:'html'}], corePlugins:{preflight:false}})]).process('@tailwind utilities;', {from:undefined})
  const declarations: Record<string, string> = {}
  css.root.walkRules(rule => {
    if (classes.has(rule.selector.slice(1).replace(/\\(.)/g, '$1'))) rule.walkDecls(declaration => {declarations[declaration.prop] = declaration.value})
  })
  expect(declarations).toMatchObject({display:'flex', width:'100%', 'min-width':'0px', 'align-items':'center', gap:'0.375rem', 'min-height':'1.5rem', 'padding-top':'0.125rem', 'padding-bottom':'0.125rem'})
})

it('紧凑思考摘要保留折叠语义、截断和流式提示，原文不进入摘要', () => {
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    for (const streaming of [false, true]) {
      const original = 'Original thought 原文😀'
      const html = renderToStaticMarkup(<ThinkingBlock block={{kind:'thinking', id:'compact-thought', at:1, text:original, partial:'stopped', streaming}} />)
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

it('展开详情先显示执行信息，原始输入输出位于卡片内，学习入口收尾', () => {
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    for (const failed of [false, true]) {
      const output = 'Original result 原文😀'
      const html = renderToStaticMarkup(<ToolBlock defaultOpen block={{kind:'tool', id:'detail-hierarchy', callId:'detail-hierarchy', at:1, name:'bash', status:failed?'error':'ok', input:{command:'python3 verify.py'}, durationMs:165, display:{exit_code:failed?2:0}, output}} />)
      const header = html.match(/<header\b[^>]*>([\s\S]*?)<\/header>/)!
      const footer = html.match(/<footer\b[^>]*>([\s\S]*?)<\/footer>/)!
      expect(header[1]).toContain('bash')
      expect(header[1]).toContain(t('ui.toolBlock.duration', {duration:'165ms'}))
      expect(header[1]).toContain(t('ui.toolBlock.exitCode', {code:failed?2:0}))
      expect(header[1]).not.toContain(t('ui.academy.learnTool'))
      expect(footer[1]).toContain(t('ui.academy.learnTool'))
      expect(html.indexOf('python3 verify.py', header.index)).toBeGreaterThan(header.index!)
      expect(html.indexOf(output)).toBeGreaterThan(header.index!)
      expect(html.indexOf(output)).toBeLessThan(footer.index!)
      expect(html).toContain('role="group" aria-label="bash" data-tool-detail=""')
    }
  }
})
