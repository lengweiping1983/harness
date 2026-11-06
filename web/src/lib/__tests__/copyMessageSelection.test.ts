import type {ClipboardEvent} from 'react'
import {expect, it, vi} from 'vitest'
import {copyMessageSelection} from '../copyMessageSelection'

function fixture(text = '开发一款 3D CS 游戏，需要有真实感') {
  const inside = {}, outside = {}
  const range = {startContainer: inside, endContainer: inside}
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
