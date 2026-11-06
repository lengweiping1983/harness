import type {ClipboardEvent} from 'react'
import {expect, it, vi} from 'vitest'
import {copyMessageSelection} from '../copyMessageSelection'

function fixture(text = '开发一款 3D CS 游戏，需要有真实感', sourceText = text) {
  const inside = {}, outside = {}
  const range = {startContainer: inside, endContainer: inside, toString: () => sourceText}
  const selection = {isCollapsed: false, rangeCount: 1, getRangeAt: () => range, toString: () => text}
  const data = new Map([['text/html', '<p>' + text + '</p>']])
  const event = {
    defaultPrevented: false,
    currentTarget: {ownerDocument: {getSelection: (): typeof selection | null => selection}, contains: (node: unknown) => node === inside},
    clipboardData: {clearData: vi.fn(() => data.clear()), setData: vi.fn((type: string, value: string) => data.set(type, value))},
    preventDefault: vi.fn(),
  }
  const copy = () => copyMessageSelection(event as unknown as ClipboardEvent<HTMLElement>)
  return {event, data, selection, range, outside, copy}
}

it.each(['开发一款 3D CS 游戏，需要有真实感', 'CS 游戏', '  原文 😀\n\nSecond paragraph\n'])('选中正文只复制纯文本且逐字保留选择：%s', text => {
  const {event, data, copy} = fixture(text)
  copy()
  expect([...data]).toEqual([['text/plain', text]])
  expect(event.preventDefault).toHaveBeenCalledOnce()
})

it.each([
  {selected: '开发一款 3D CS 游戏，需要有真实感\n\n', source: '开发一款 3D CS 游戏，需要有真实感', expected: '开发一款 3D CS 游戏，需要有真实感'},
  {selected: '用户正文\n\n\n\n', source: '用户正文', expected: '用户正文'},
  {selected: '局部文字 😀\n\n', source: '局部文字 😀', expected: '局部文字 😀'},
  {selected: '第一段\n\n第二段\n\n\n\n', source: '第一段\n第二段', expected: '第一段\n\n第二段'},
  {selected: '  保留缩进与空格  \n\n', source: '  保留缩进与空格  ', expected: '  保留缩进与空格  '},
  {selected: '代码原文\n\n\n', source: '代码原文\n', expected: '代码原文\n'},
  {selected: '原有空行\n\n\n\n', source: '原有空行\n\n', expected: '原有空行\n\n'},
  {selected: 'CRLF 原文\n\n', source: 'CRLF 原文\r\n', expected: 'CRLF 原文\r\n'},
  {selected: '末行空格\n  \n\t', source: '末行空格', expected: '末行空格'},
  {selected: '空白末行\n  \n\n', source: '空白末行\n  ', expected: '空白末行\n  '},
])('只移除选区布局补出的尾部换行：$source', ({selected, source, expected}) => {
  // Selection 的可见文本可以包含块布局分隔，Range 的文本不包含这些布局换行。
  const {data, event, copy} = fixture(selected, source)
  copy()
  expect([...data]).toEqual([['text/plain', expected]])
  expect(event.preventDefault).toHaveBeenCalledOnce()
})

it.each([
  {selected: '部分选择', source: '部分选择\n\n'},
  {selected: '只选一个换行\n', source: '只选一个换行\n\n'},
])('不为没有额外换行的可见选区补写换行：$selected', ({selected, source}) => {
  const {data, copy} = fixture(selected, source)
  copy()
  expect(data.get('text/plain')).toBe(selected)
})

it.each(['startContainer', 'endContainer'] as const)('选择的 %s 超出正文时保留浏览器行为', endpoint => {
  const {event, range, outside, copy} = fixture()
  range[endpoint] = outside
  copy()
  expect(event.clipboardData.clearData).not.toHaveBeenCalled()
  expect(event.clipboardData.setData).not.toHaveBeenCalled()
  expect(event.preventDefault).not.toHaveBeenCalled()
})

it.each(['handled', 'collapsed', 'multiple', 'missing', 'empty'])('不拦截 %s 复制', kind => {
  const {event, selection, copy} = fixture(kind === 'empty' ? '' : 'Original 原文')
  if (kind === 'handled') event.defaultPrevented = true
  if (kind === 'collapsed') selection.isCollapsed = true
  if (kind === 'multiple') selection.rangeCount = 2
  if (kind === 'missing') event.currentTarget.ownerDocument.getSelection = () => null
  copy()
  expect(event.clipboardData.clearData).not.toHaveBeenCalled()
  expect(event.clipboardData.setData).not.toHaveBeenCalled()
  expect(event.preventDefault).not.toHaveBeenCalled()
})
