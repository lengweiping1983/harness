import {describe, expect, it} from 'vitest'
import {stabilizeMath, scanFences, segmentStream, isRendererInfo} from '../streaming'
import {normalizeMediaTags} from '../mediaTags'
import {remarkRenderer} from '../rendererRemark'

describe('streamed math boundaries', () => {
  it.each([
    '$', '$x$ and $', '$$\n', '$x$', '$x$ and $y$', '$$\nx\n$$', '$$\nx\n$$\n\n$x$',
    'inline $$x$$ done', '$$$\nx\n$$$$', '\\$10', '`$VAR`',
    '``a `$VAR` b``', '```sh\necho $VAR\n```', '~~~sh\necho $VAR',
    '$x\n\nordinary paragraph', '$x\n```sh\necho $VAR\n```',
  ])('preserves completed formulas and literal text: %s', source => {
    expect(stabilizeMath(source)).toBe(source)
  })
  it.each([
    ['$x', '$x$'], ['$x$ and $y', '$x$ and $y$'],
    ['$$\nx', '$$\nx\n$$'], ['$$\nx\n', '$$\nx\n$$'],
    ['inline $$x', 'inline $$x$$'], ['$$$\nx', '$$$\nx\n$$$'],
    ['`literal $VAR` then $x', '`literal $VAR` then $x$'],
    ['``literal `$VAR` here`` then $x', '``literal `$VAR` here`` then $x$'],
    ['\\$10 then $x', '\\$10 then $x$'],
    ['$$\n$x\n', '$$\n$x\n$$'],
  ])('closes only the final unmatched delimiter: %s', (source, result) => {
    expect(stabilizeMath(source)).toBe(result)
    expect(stabilizeMath(result)).toBe(result)
  })
  it('retains completed formulas on a long line and fenced source offsets', () => {
    const source = '$x$ '.repeat(10000)
    expect(stabilizeMath(source)).toBe(source)
    const fence = '```sh\necho $VAR\n```'
    const blocks = scanFences(fence)
    expect(blocks).toHaveLength(1)
    expect(fence.slice(blocks[0].bodyStart, blocks[0].bodyEnd)).toBe('echo $VAR\n')
  })
})

describe('multiple raw media elements', () => {
  it('consumes each closing tag after its own opening tag without losing surrounding text', () => {
    const source = '<video src="one.mp4"></video>middle<video><source src="two.mp4"></video>after<audio src="one.mp3"></audio><audio src="two.mp3"></audio>'
    expect(normalizeMediaTags(source)).toBe('[one.mp4](one.mp4)middle[two.mp4](two.mp4)after[one.mp3](one.mp3)[two.mp3](two.mp3)')
  })
  it('preserves missing sources and fenced HTML while handling an unclosed element', () => {
    const source = '```html\n<video src="example.mp4"></video>\n```\n<video>no source</video><img src="chart.png" alt="Chart"><video src="open.mp4">'
    expect(normalizeMediaTags(source)).toBe('```html\n<video src="example.mp4"></video>\n```\n<video>no source</video>![Chart](chart.png)[open.mp4](open.mp4)')
  })
})

describe('renderer fences', () => {
  it.each([
    ['html type="renderer"', true],
    ["html type='renderer'", true],
    ['html type=renderer', true],
    ['HTML type = "renderer"', true],
    ['html subtype=renderer', false],
    ['html type=renderer-other', false],
    ['html type="renderer-extra"', false],
    ['html type="renderer', false],
    ['html title="example type=renderer"', false],
    ['html type=renderer type=widget', false],
    ['html type=widget type=renderer', false],
    ['html', false],
    ['html type="widget"', false],
    ['svg', false],
    ['', false],
  ])('recognizes renderer info string: %s', (raw, expected) => {
    expect(isRendererInfo(raw)).toBe(expected)
  })

  it('does not close a top-level renderer with a quoted fence inside its source', () => {
    const source = '```html type="renderer"\n<pre>\n> ```\n</pre>\n```'
    const blocks = scanFences(source)
    expect(blocks).toHaveLength(1)
    expect(blocks[0].closed).toBe(true)
    expect(blocks[0].code).toBe('<pre>\n> ```\n</pre>')
    expect(segmentStream(source)[0]).toMatchObject({kind: 'renderer', code: blocks[0].code, closed: true})
  })

  it('marks renderer fences in scanFences regardless of closure', () => {
    const closed = scanFences('```html type="renderer"\n<h1>hi</h1>\n```')
    expect(closed).toHaveLength(1)
    expect(closed[0].lang).toBe('html')
    expect(closed[0].renderer).toBe(true)
    const open = scanFences('```html type="renderer"\n<h1>hi</h1>')
    expect(open[0].renderer).toBe(true)
    expect(open[0].closed).toBe(false)
  })

  it('keeps plain html fences as charts, not renderers', () => {
    const blocks = scanFences('```html\n<p>code</p>\n```')
    expect(blocks[0].renderer).toBe(false)
  })

  it('splits top-level renderer fences into renderer segments with closure state', () => {
    const segments = segmentStream('before\n\n```html type="renderer"\n<div>a</div>\n```\n\nafter')
    expect(segments.map(s => s.kind)).toEqual(['markdown', 'renderer', 'markdown'])
    const renderer = segments.find(s => s.kind === 'renderer') as {kind: 'renderer'; code: string; closed: boolean}
    expect(renderer.code).toBe('<div>a</div>')
    expect(renderer.closed).toBe(true)
    const open = segmentStream('```html type="renderer"\n<div>a</div>')
    expect((open[0] as {kind: string}).kind).toBe('renderer')
    expect((open[0] as {closed: boolean}).closed).toBe(false)
  })

  it('keeps indented renderer fences inside markdown for the remark fallback', () => {
    const segments = segmentStream('- item\n\n  ```html type="renderer"\n  <p>x</p>\n  ```')
    expect(segments.map(s => s.kind)).toEqual(['markdown'])
  })

  it('remarkRenderer tags html+renderer code nodes for the code component', () => {
    const tree = {
      type: 'root',
      children: [
        {type: 'paragraph', children: [{type: 'text', value: 'lead'}]},
        {type: 'code', lang: 'html', meta: ' type="renderer"', value: '<p>x</p>'},
        {type: 'code', lang: 'html', meta: null, value: '<p>plain</p>'},
        {type: 'code', lang: 'svg', meta: ' type="renderer"', value: '<svg/>'},
      ],
    }
    remarkRenderer()(tree as never)
    const [lead, renderer, plain, svg] = tree.children as Array<{data?: Record<string, unknown>}>
    expect(lead.data).toBeUndefined()
    expect(renderer.data?.hProperties).toEqual({'data-harness-renderer': 'true', 'data-harness-closed': 'false'})
    expect(plain.data?.hProperties).toEqual({'data-harness-closed': 'false'})
    expect(svg.data?.hProperties).toEqual({'data-harness-closed': 'false'})
  })
})
