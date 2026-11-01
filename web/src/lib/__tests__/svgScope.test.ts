import {expect, it} from 'vitest'
import {parseProgressiveSvg, scopeSvg} from '../progressiveSvg'

it('isolates gradient, marker and href references without changing the source tree', () => {
  const parsed = parseProgressiveSvg('<svg viewBox="0 0 100 100"><defs><linearGradient id="paint"><stop offset="0" /></linearGradient><marker id="arrow"/></defs><rect id="box" fill="url(#paint)" marker-end="url(#arrow)"/><use href="#box"/></svg>')
  const first = scopeSvg(parsed, 'first')!
  const second = scopeSvg(parsed, 'second')!
  expect(first.markup).toContain('id="first-paint"')
  expect(first.markup).toContain('fill="url(#first-paint)"')
  expect(first.markup).toContain('href="#first-box"')
  expect(first.markup).toContain('marker-end="url(#first-arrow)"')
  expect(second.markup).toContain('id="second-paint"')
  expect(second.markup).not.toContain('first-')
  expect(parsed!.markup).toContain('id="paint"')
})

it('keeps accessible titles bound to their own diagram', () => {
  const parsed = scopeSvg(parseProgressiveSvg('<svg viewBox="0 0 100 100" aria-labelledby="label"><title id="label">Original title</title></svg>'), 'chart')!
  expect(parsed.markup).toContain('id="chart-label"')
  expect(parsed.markup).toContain('aria-labelledby="chart-label"')
})
