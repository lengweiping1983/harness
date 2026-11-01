import {expect, it} from 'vitest'
import {mergeToolOutput} from '../toolOutput'

const frame = {output_seq:1, stdout:'Original 原文 😀', stderr:'', stdout_offset:0, stderr_offset:0}
it('rejects coerced sequence and offsets without replacing confirmed preview', () => {
  const original = mergeToolOutput(undefined, frame)!
  for (const field of ['output_seq', 'stdout_offset', 'stderr_offset']) {
    for (const value of [true, false, null, '2', '', [], {}, -1, 1.5, Infinity, NaN]) {
      expect(mergeToolOutput(original, {...frame, output_seq:2, [field]:value})).toBe(original)
      expect(mergeToolOutput(undefined, {...frame, [field]:value})).toBeUndefined()
    }
  }
  expect(original.stdout).toBe('Original 原文 😀')
})
it('keeps Unicode code point offsets, overlap and stale sequence protection', () => {
  const original = mergeToolOutput(undefined, {...frame, stdout:'中😀'})!
  expect(original.stdoutEnd).toBe(2)
  const next = mergeToolOutput(original, {...frame, output_seq:2, stdout:'😀next', stdout_offset:1})!
  expect(next.stdout).toBe('中😀next')
  expect(next.stdoutEnd).toBe(6)
  expect(mergeToolOutput(next, frame)).toBe(next)
})
