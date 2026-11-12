import { describe, expect, it } from 'vitest'
import { detectTrigger, isInCodeContext, scoreCandidate } from '../trigger'

describe('inline code completion boundaries', () => {
  it('terminates scanning and excludes open inline code', () => {
    expect(detectTrigger('`code @file', 11)).toBeNull()
    expect(isInCodeContext('`code` @file', 12)).toBe(false)
    expect(detectTrigger('`code` @file', 12)?.query).toBe('file')
  })
  it('counts escaped backticks without leaking regex cursor state', () => {
    for (let i = 0; i < 3; i++) {
      expect(isInCodeContext('\\` @file', 8)).toBe(false)
      expect(isInCodeContext('\\\\` @file', 9)).toBe(true)
      expect(isInCodeContext('plain @file', 11)).toBe(false)
    }
  })
  it('keeps completion disabled inside fenced code', () => {
    expect(detectTrigger('```ts\n@file', 11)).toBeNull()
    expect(detectTrigger('```ts\ncode\n```\n@file', 20)?.query).toBe('file')
  })
})


describe('completion delimiters and highlight offsets', () => {
  it('requires matching fence characters, lengths and closing suffix', () => {
    for (const text of ['```sh\n~~~\n@file', '````sh\n```\n@file', '```sh\n```still code\n@file']) {
      expect(detectTrigger(text, text.length)).toBeNull()
    }
    const closed = '````sh\n`````\n@file'
    expect(detectTrigger(closed, closed.length)?.query).toBe('file')
  })
  it('pairs inline backtick runs by length', () => {
    for (const text of ['`` code ` @file', '`` code @file']) expect(detectTrigger(text, text.length)).toBeNull()
    const closed = '`` code ` x `` @file'
    expect(detectTrigger(closed, closed.length)?.query).toBe('file')
  })
  it('highlights the matching title prefix or filename prefix', () => {
    const candidate = { title: 'src/report.txt', insert: 'src/report.txt', kind: 'file' as const }
    expect(scoreCandidate(candidate, 'src')?.hits).toEqual([0, 1, 2])
    expect(scoreCandidate(candidate, 'rep')?.hits).toEqual([4, 5, 6])
  })
})


it('maps case expansion and surrogate pairs to original highlight indices', () => {
  const candidate = { title: 'İ/😀Report.txt', insert: '', kind: 'file' as const }
  expect(scoreCandidate(candidate, 'rep')?.hits).toEqual([4, 5, 6])
  expect(scoreCandidate(candidate, '😀r')?.hits).toEqual([2, 3, 4])
  expect(scoreCandidate({ ...candidate, title: '😀' }, '😀')?.hits).toEqual([0, 1])
  expect(scoreCandidate({ ...candidate, title: 'İx' }, 'i')?.hits).toEqual([0])
})


it('preserves context-sensitive lowercase matching and exact-match ranking', () => {
  const candidate = { title: 'ΟΣ', insert: '', kind: 'file' as const }
  expect(scoreCandidate(candidate, 'ος')).toMatchObject({ score: 1000, hits: [0, 1] })
  expect(scoreCandidate({ ...candidate, title: 'ΟΣ file' }, 'ος')?.hits).toEqual([0, 1])
})
