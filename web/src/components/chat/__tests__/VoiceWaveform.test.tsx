import {renderToStaticMarkup} from 'react-dom/server'
import {expect, it, vi} from 'vitest'
import {VoiceWaveform, updateVoiceWaveform} from '../VoiceWaveform'

it('keeps the actual waveform compact, decorative and free from fake looping motion', () => {
  const html = renderToStaticMarkup(<VoiceWaveform elementRef={{current: null}} />)
  expect(html.match(/<line /g)).toHaveLength(5)
  expect(html).toContain('aria-hidden="true"')
  expect(html).not.toContain('animate-')
})
it('maps actual loudness to bounded symmetric strokes and returns to the quiet shape', () => {
  const lines = Array.from({length: 5}, () => ({setAttribute: vi.fn()}))
  const element = {querySelectorAll: () => lines} as unknown as SVGSVGElement
  const heights = () => lines.map(line => {
    const values = Object.fromEntries(line.setAttribute.mock.calls)
    return Number(values.y2) - Number(values.y1)
  })
  updateVoiceWaveform(element, 0)
  expect(heights()).toEqual([2, 2, 2, 2, 2])
  updateVoiceWaveform(element, .2)
  const quiet = heights()
  updateVoiceWaveform(element, 1)
  expect(heights().every((height, index) => height > quiet[index])).toBe(true)
  expect(heights()[2]).toBe(20)
  expect(heights()[0]).toBe(heights()[4])
  updateVoiceWaveform(element, NaN)
  expect(heights()).toEqual([2, 2, 2, 2, 2])
})
