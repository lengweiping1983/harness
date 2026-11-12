import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {Markdown} from '@/components/markdown/MarkdownContent'
import {setLocale, t} from '@/i18n'

vi.mock('@/hooks/useDialogFocus', () => ({useDialogFocus: () => {}}))
// 仅替换需要浏览器 DOM 的沙箱边界；保留产品 Markdown、清洗器、渲染卡片及源码分流。
const sandbox = vi.hoisted(() => ({codes: [] as string[]}))
vi.mock('@/components/ui/HtmlSandboxFrame', () => ({HtmlSandboxFrame: ({code}: {code: string}) => {
  sandbox.codes.push(code)
  return <iframe title="test sandbox" />
}}))
beforeEach(() => vi.stubGlobal('document', {activeElement: null, documentElement: {}}))
afterEach(() => { sandbox.codes.length = 0; setLocale('en'); vi.unstubAllGlobals() })
const code = '<div><svg viewBox="0 0 10 10"><circle r="2"/></svg><img src="https://example.com/a.png"><script>window.answer=42</script></div>'
const fence = (info = 'html type="renderer"', close = true) => `\`\`\`${info}\n${code}${close ? '\n```' : ''}`
const nested = (source: string, kind: string) => kind === 'quote' ? source.split('\n').map(line => '> ' + line).join('\n') : kind === 'list' ? '- item\n\n' + source.split('\n').map(line => '  ' + line).join('\n') : source

describe('actual Markdown visualization pipeline', () => {
  it.each(['top', 'quote', 'list', 'document'])('renders complete renderer HTML through sanitization in %s', kind => {
    const html = renderToStaticMarkup(<Markdown streaming fileSource={kind === 'document' ? {path: '/tmp/readme.md', root: '/tmp'} : undefined}>{nested(fence(), kind)}</Markdown>)
    expect(html).toContain('renderer-frame')
    expect(html).toContain('test sandbox')
    expect(sandbox.codes).toEqual([code])
    if (kind === 'list') expect(html).toContain('<ul>')
    if (kind === 'quote') expect(html).toContain('<blockquote>')
  })
  it.each(['top', 'quote', 'list', 'document'])('never executes unfinished HTML during or after streaming in %s', kind => {
    for (const streaming of [true, false]) {
      for (const info of ['html', 'html type="renderer"']) {
        const html = renderToStaticMarkup(<Markdown streaming={streaming} fileSource={kind === 'document' ? {path: '/tmp/readme.md', root: '/tmp'} : undefined}>{nested(fence(info, false), kind)}</Markdown>)
        expect(html).not.toContain('<iframe')
        if (info.includes('renderer')) expect(html).toContain(streaming ? t('ui.renderer.rendering') : t('ui.renderer.incomplete'))
      }
    }
    expect(sandbox.codes).toEqual([])
  })
  it('keeps a closed nested renderer executable while subsequent prose streams', () => {
    renderToStaticMarkup(<Markdown streaming>{nested(fence(), 'quote') + '\n\nStill writing…'}</Markdown>)
    expect(sandbox.codes).toEqual([code])
  })
  it('preserves ordinary HTML preview, XML source, and multiple SVG segments', () => {
    const html = renderToStaticMarkup(<Markdown>{fence('html') + '\n\n' + fence('xml') + '\n\n```svg\n<svg viewBox="0 0 10 10"><rect width="5" height="5"/></svg>\n```'}</Markdown>)
    expect(sandbox.codes).toEqual([code])
    expect(html).not.toContain('renderer-frame')
    expect(html).toContain('svg')
  })
  it('uses complete localized controls without changing the HTML payload', () => {
    for (const language of ['en', 'zh-CN', 'en'] as const) {
      setLocale(language)
      const html = renderToStaticMarkup(<Markdown workspaceLinks={false}>{nested(fence(), 'quote')}</Markdown>)
      for (const key of ['ui.renderer.title', 'ui.widget.viewCode', 'ui.widget.download', 'ui.widget.zoomIn']) expect(html).toContain(t(key))
    }
    expect(sandbox.codes).toEqual([code, code, code])
  })
  it.each(['html subtype="renderer"', 'html type="renderer-other"', 'html title="example type=renderer"', 'svg type="renderer"'])('does not promote unrelated metadata: %s', info => {
    const html = renderToStaticMarkup(<Markdown>{nested(fence(info), 'quote')}</Markdown>)
    expect(html).not.toContain('renderer-frame')
  })
  it.each(['~~~html type=renderer\n<p>ok</p>\n~~~', '````HTML type=renderer\r\n<p>ok</p>\r\n````'])('supports tilde, CRLF, and uppercase fences: %s', source => {
    const html = renderToStaticMarkup(<Markdown>{nested(source, 'quote')}</Markdown>)
    expect(html).toContain('test sandbox')
  })
})
