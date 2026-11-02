import {expect, it} from 'vitest'
import {createVoiceLevelMeter} from '../voiceLevel'

const tone = (peak: number, count = 800, bias = 0) => Float32Array.from({length: count}, (_, index) => bias + Math.sin(index * Math.PI / 20) * peak)
it('uses real energy, distinguishes quiet speech from loud speech and falls back to silence', () => {
  const values: number[] = []
  const meter = createVoiceLevelMeter(16000, value => values.push(value))
  meter(tone(0))
  expect(values).toEqual([0])
  meter(tone(.01))
  const quiet = values.at(-1)!
  meter(tone(.25))
  const loud = values.at(-1)!
  expect(quiet).toBeGreaterThan(0)
  expect(loud).toBeGreaterThan(quiet)
  meter(tone(0, 800 * 20))
  expect(values.at(-1)).toBe(0)
  expect(values.every(value => value >= 0 && value <= 1)).toBe(true)
})
it('rejects DC bias and very faint noise instead of inventing speech', () => {
  const values: number[] = []
  const meter = createVoiceLevelMeter(16000, value => values.push(value))
  meter(new Float32Array(800).fill(.5))
  meter(tone(.0001))
  expect(values).toEqual([0, 0])
})
it('keeps the same time window across worklet chunks without changing captured samples', () => {
  const split: number[] = [], whole: number[] = []
  const splitMeter = createVoiceLevelMeter(48000, level => split.push(level))
  const source = tone(.2, 4800)
  const original = source.slice()
  for (let index = 0; index < source.length; index += 128) splitMeter(source.subarray(index, index + 128))
  createVoiceLevelMeter(48000, level => whole.push(level))(source)
  expect(split).toEqual(whole)
  expect(split).toHaveLength(2)
  expect(source).toEqual(original)
})
