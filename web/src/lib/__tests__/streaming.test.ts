import {describe, expect, it} from 'vitest'
import {stabilizeMath, scanFences} from '../streaming'
import {normalizeMediaTags} from '../mediaTags'

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
