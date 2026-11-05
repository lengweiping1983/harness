import {afterEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {ToolBlock} from '@/components/chat/blocks/ToolBlock'
import {setLocale, t} from '@/i18n'
import type {ToolBlockState} from '@/types'

// 仅替换浏览器沙箱边界，保留真实工具详情与卡片呈现。
vi.mock('@/hooks/useDialogFocus', () => ({useDialogFocus: () => {}}))
vi.mock('@/components/ui/HtmlSandboxFrame', () => ({HtmlSandboxFrame: ({title}: {title: string}) => <iframe title={title} />}))

afterEach(() => setLocale('en'))

const block: ToolBlockState = {
  kind: 'tool', id: 'widget', callId: 'widget-call', at: 1, name: 'show_widget', status: 'ok',
  input: {title: 'Original preview 原文', widget_code: '<button>Interact</button>'},
  display: {widget: {title: 'Original preview 原文', kind: 'html', code: '<button>Interact</button>'}},
  output: 'Widget delivered', durationMs: 1,
}

it('keeps the preview visible when details are collapsed and places expanded details before the preview', () => {
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    for (const open of [false, true]) {
      const html = renderToStaticMarkup(<ToolBlock block={block} defaultOpen={open} sessionId="widget-owner" />)
      expect(html).toContain(`aria-expanded="${open}"`)
      expect(html.match(/<iframe /g)).toHaveLength(1)
      expect(html).toContain('title="Original preview 原文"')
      expect(html.includes(t('ui.academy.learnTool'))).toBe(open)
      if (open) {
        const detail = html.indexOf('data-tool-detail')
        const learning = html.indexOf(t('ui.academy.learnTool'))
        const input = html.indexOf(t('ui.components.chat.blocks.ToolBlock.2087c777c06f'))
        const preview = html.indexOf('<figure')
        expect(detail).toBeGreaterThan(0)
        expect(input).toBeGreaterThan(detail)
        expect(learning).toBeGreaterThan(input)
        expect(preview).toBeGreaterThan(learning)
        expect(html).toContain('Widget delivered')
        const details = html.match(/<div id="[^"]+"[^>]*data-tool-detail="" class="([^"]+)">/)?.[1]?.split(' ')
        expect(details).toEqual(expect.arrayContaining(['border', 'border-line', 'rounded-xl']))
        expect(details).not.toContain('border-l')
        expect(html.match(/<footer\b[^>]*>([\s\S]*?)<\/footer>/)?.[1]).toContain(t('ui.academy.learnTool'))
      }
    }
  }
})

it('retains a loading preview and explicit failure diagnostics without fabricating a completed widget', () => {
  const pending = renderToStaticMarkup(<ToolBlock block={{...block, status: 'running', display: undefined, output: undefined}} />)
  expect(pending).toContain('role="status"')
  expect(pending).toContain('Original preview 原文')
  expect(pending).not.toContain('<iframe')
  const failed = renderToStaticMarkup(<ToolBlock block={{...block, status: 'error', output: 'Original failure 原文'}} defaultOpen />)
  expect(failed).toContain('Original failure 原文')
  expect(failed).toContain('lucide-triangle-alert')
  expect(failed).not.toContain('<iframe')
})
