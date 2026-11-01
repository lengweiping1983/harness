import {describe, expect, it} from 'vitest'
import {parseUnifiedDiff, diffWords, pairRowsForWords, createWordDiffBudget, type DiffRow} from '../diff'

describe('unified diff source boundaries', () => {
  it('keeps header-like source rows and Unicode separators inside their hunk', () => {
    const diff = parseUnifiedDiff('--- a/sample.txt\n+++ b/sample.txt\n@@ -1,2 +1,2 @@\n--- old\n-a\u2028-fake\n+++ new\n+a\u2028+fake\n')
    expect(diff.files).toEqual([{from: 'a/sample.txt', to: 'b/sample.txt'}])
    expect([diff.additions, diff.deletions]).toEqual([2, 2])
    expect(diff.hunks[0].rows).toEqual([
      {kind: 'del', text: '-- old', oldNo: 1, newNo: null},
      {kind: 'del', text: 'a\u2028-fake', oldNo: 2, newNo: null},
      {kind: 'add', text: '++ new', oldNo: null, newNo: 1},
      {kind: 'add', text: 'a\u2028+fake', oldNo: null, newNo: 2},
    ])
  })

  it('recognizes following file headers after a completed hunk and keeps end-of-file metadata', () => {
    const parsed = parseUnifiedDiff('--- a/one\n+++ b/one\n@@ -1 +1 @@\n-old\n+new\n\\ No newline at end of file\n--- a/two\n+++ b/two\n@@ -0,0 +1 @@\n+two\n')
    expect(parsed.files).toEqual([{from: 'a/one', to: 'b/one'}, {from: 'a/two', to: 'b/two'}])
    expect(parsed.hunks).toHaveLength(2)
    expect(parsed.hunks[0].rows.at(-1)?.kind).toBe('meta')
    expect(parsed.hunks[1].rows).toEqual([{kind: 'add', text: 'two', oldNo: null, newNo: 1}])
  })
})

describe('bounded word highlighting', () => {
  it('retains useful word highlighting on ordinary lines and preserves all characters', () => {
    const {left, right} = diffWords('const value = "中文😀"', 'const value = "English😀"')
    expect(left.map(part => part.text).join('')).toBe('const value = "中文😀"')
    expect(right.map(part => part.text).join('')).toBe('const value = "English😀"')
    expect(left.some(part => !part.changed && part.text.includes('const value'))).toBe(true)
    expect(right.some(part => part.changed && part.text.includes('English'))).toBe(true)
  })

  it('shows complete long changed lines without allocating a quadratic word table', () => {
    const before = 'a, '.repeat(20000), after = 'b, '.repeat(20000)
    expect(diffWords(before, after)).toEqual({left: [{text: before, changed: true}], right: [{text: after, changed: true}]})
    expect(diffWords(before, before)).toEqual({left: [{text: before, changed: false}], right: [{text: before, changed: false}]})
  })

  it('shares the budget across hunks while keeping their original rows and complete text', () => {
    const budget = createWordDiffBudget()
    const makeRows = (text: string): DiffRow[] => [
      {kind: 'del', text, oldNo: 1, newNo: null},
      {kind: 'add', text: text.replaceAll('a', 'b'), oldNo: null, newNo: 1},
    ]
    const first = makeRows('a, '.repeat(250))
    expect(pairRowsForWords(first, budget)[0].segments?.some(segment => !segment.changed)).toBe(true)
    const second = makeRows('a, '.repeat(250))
    const paired = pairRowsForWords(second, budget)
    expect(paired.map(item => item.row)).toEqual(second)
    expect(paired[0].segments).toEqual([{text: second[0].text, changed: true}])
    expect(paired[1].segments).toEqual([{text: second[1].text, changed: true}])
  })
})
