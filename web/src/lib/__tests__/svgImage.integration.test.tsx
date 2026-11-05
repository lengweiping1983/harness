import {afterEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {Markdown} from '@/components/markdown/MarkdownContent'
import {WidgetCard} from '@/components/chat/blocks/WidgetCard'
import {SvgImage} from '@/components/ui/SvgImage'
import {setLocale, t} from '@/i18n'

vi.mock('@/hooks/useDialogFocus', () => ({useDialogFocus: () => {}}))
// 服务端没有布局阶段，浏览器布局钩子沿普通 effect 边界处理。
vi.mock('react', async original => {const react = await original<typeof import('react')>(); return {...react, useLayoutEffect: react.useEffect}})
afterEach(() => setLocale('en'))
const source = '<svg viewBox="0 0 1600 900"><style>@keyframes fly{to{transform:translateX(1200px)}} .arrow{animation:fly 4s infinite}</style><path class="arrow" d="M0 0 L20 0"><animate attributeName="opacity" values="0;1;0" dur="4s" repeatCount="indefinite"/></path></svg>'
const imageSource = (html: string) => {
  const src = /<img[^>]+src="([^"]+)"/.exec(html)?.[1]
  expect(src).toMatch(/^data:image\/svg\+xml;charset=utf-8,/)
  return decodeURIComponent(src!.split(',').slice(1).join(','))
}

it('routes fenced, bare, nested Markdown and widget SVG animations into images with localized controls', () => {
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    for (const content of [`\`\`\`svg\n${source}\n\`\`\``, source, `> \`\`\`svg\n> ${source}\n> \`\`\``]) {
      const html = renderToStaticMarkup(<Markdown>{content}</Markdown>)
      const svg = imageSource(html)
      expect(svg).toContain('@keyframes fly')
      expect(svg).toContain('values="0;1;0"')
      expect(html).not.toContain('<style>')
      expect(html).not.toContain('<animate ')
      expect(html).toContain(t('ui.components.markdown.LiveChart.ad9a4e83ff36'))
    }
    const widget = renderToStaticMarkup(<WidgetCard payload={{kind: 'svg', title: '动画 Original', code: source, chars: source.length, loadingMessages: []}} />)
    expect(imageSource(widget)).toContain('@keyframes fly')
    expect(widget).toContain('alt="动画 Original"')
    expect(widget).toContain(t('ui.widget.download'))
    expect(widget).not.toContain('<style>')
  }
})

it('changes the image resource on replay while preserving CSS, SMIL and dimensions', () => {
  const first = renderToStaticMarkup(<SvgImage markup={source} alt="SVG" />)
  const replay = renderToStaticMarkup(<SvgImage markup={source} alt="SVG" replay={1} />)
  expect(replay).not.toBe(first)
  expect(imageSource(first)).toMatch(/;replay:0 -->$/)
  expect(imageSource(replay)).toMatch(/;replay:1 -->$/)
  expect(imageSource(replay).split('<!-- preview:')[0]).toBe(source)
  const pair = renderToStaticMarkup(<><SvgImage markup={source} alt="First" /><SvgImage markup={source} alt="Second" /></>)
  const urls = [...pair.matchAll(/<img[^>]+src="([^"]+)"/g)].map(match => match[1])
  expect(urls).toHaveLength(2)
  expect(urls[0]).not.toBe(urls[1])
})
