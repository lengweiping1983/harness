import {expect, it, vi} from 'vitest'
import {copyMessageSelection, observeMessageSelectionCopy} from '../copyMessageSelection'

function fixture(text = '开发一款 3D CS 游戏，需要有真实感', sourceText = text) {
  const inside = {nodeType: 3, parentElement: {closest: (): object | null => body}}, outside = {nodeType: 3, parentElement: {closest: () => null}}
  const fragment = {textContent: '', querySelector: (_selector: string): object | null => null, querySelectorAll: () => [] as {remove: () => void}[]}
  const outsideRange = {toString: () => '', setStart: vi.fn(), cloneContents: () => fragment}
  const clippedRange = {toString: () => sourceText, setEnd: vi.fn()}
  const range = {startContainer: inside, endContainer: inside, toString: () => sourceText,
    cloneRange: vi.fn().mockReturnValueOnce(outsideRange).mockReturnValueOnce(clippedRange)}
  const selection = {isCollapsed: false, rangeCount: 1, getRangeAt: () => range, toString: () => text}
  const data = new Map([['text/html', '<p>' + text + '</p>']])
  const listeners = new Set<(event: ClipboardEvent) => void>()
  const document = {
    getSelection: (): typeof selection | null => selection,
    createRange: () => ({selectNodeContents: vi.fn(), endContainer: body as unknown, endOffset: 1}),
    addEventListener: vi.fn((_name: string, listener: (event: ClipboardEvent) => void) => listeners.add(listener)),
    removeEventListener: vi.fn((_name: string, listener: (event: ClipboardEvent) => void) => listeners.delete(listener)),
  }
  const body = {ownerDocument: document, contains: (node: unknown): boolean => node === inside || node === body}
  const scope = {ownerDocument: document, contains: (node: unknown): boolean => node === body}
  const event = {
    defaultPrevented: false, target: null as {closest: () => object | null} | null,
    currentTarget: body,
    clipboardData: {clearData: vi.fn(() => data.clear()), setData: vi.fn((type: string, value: string) => data.set(type, value))},
    preventDefault: vi.fn(),
  }
  event.preventDefault.mockImplementation(() => {event.defaultPrevented = true})
  const copy = () => copyMessageSelection(event as unknown as ClipboardEvent, body as unknown as HTMLElement)
  return {event, data, selection, range, outside, copy, scope, document, listeners, outsideRange, clippedRange, body, fragment}
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

it.each(['startContainer', 'endContainer'] as const)('选择的 %s 跨到其他正文时保留浏览器行为', endpoint => {
  const {event, range, outside, copy, fragment} = fixture()
  range[endpoint] = outside
  fragment.textContent = '另一条消息'
  copy()
  expect(event.clipboardData.clearData).not.toHaveBeenCalled()
  expect(event.clipboardData.setData).not.toHaveBeenCalled()
  expect(event.preventDefault).not.toHaveBeenCalled()
})

it('三击选区终点在正文外时仍去掉四个尾部换行，不改动原选区', () => {
  const {event, data, range, outside, copy, outsideRange, clippedRange, body} = fixture('用户消息\n\n\n\n', '用户消息')
  range.endContainer = outside
  copy()
  expect([...data]).toEqual([['text/plain', '用户消息']])
  expect(outsideRange.setStart).toHaveBeenCalledWith(body, 1)
  expect(clippedRange.setEnd).toHaveBeenCalledWith(body, 1)
  expect(range.endContainer).toBe(outside)
  expect(event.preventDefault).toHaveBeenCalledOnce()
})

it.each(['img', 'svg'])('不裁掉选区末尾的 %s 附件或非装饰图形', tag => {
  const {event, range, outside, copy, fragment} = fixture()
  range.endContainer = outside
  fragment.querySelector = selector => selector.split(',').includes(tag) ? {} : null
  copy()
  expect(event.preventDefault).not.toHaveBeenCalled()
  expect(event.clipboardData.setData).not.toHaveBeenCalled()
})

it.each(['链接文字', '正文内的按钮文字'])('不裁掉选区末尾的%s', text => {
  const {event, range, outside, copy, fragment} = fixture()
  range.endContainer = outside
  fragment.textContent = text
  copy()
  expect(event.preventDefault).not.toHaveBeenCalled()
  expect(event.clipboardData.setData).not.toHaveBeenCalled()
})

it('三击越过消息图标操作栏时只从克隆片段移除操作栏', () => {
  const {range, outside, copy, fragment, data} = fixture('用户消息\n\n\n\n', '用户消息')
  range.endContainer = outside
  fragment.querySelector = () => ({})
  fragment.textContent = 'Set as goal'
  const remove = vi.fn(() => {fragment.querySelector = () => null; fragment.textContent = ''})
  fragment.querySelectorAll = () => [{remove}]
  copy()
  expect(remove).toHaveBeenCalledOnce()
  expect([...data]).toEqual([['text/plain', '用户消息']])
})

it('复制事件落在正文外时由对话观察器接收，卸载后移除同一监听器', () => {
  const {scope, document, listeners, event, data, range, outside} = fixture('用户消息\n\n\n\n', '用户消息')
  range.endContainer = outside
  const cleanup = observeMessageSelectionCopy(scope as unknown as HTMLElement)
  expect(document.addEventListener).toHaveBeenCalledWith('copy', expect.any(Function))
  expect(listeners.size).toBe(1)
  for (const listener of listeners) listener({...event, currentTarget: document} as unknown as ClipboardEvent)
  expect([...data]).toEqual([['text/plain', '用户消息']])
  const listener = [...listeners][0]
  cleanup()
  expect(document.removeEventListener).toHaveBeenCalledWith('copy', listener)
  expect(listeners.size).toBe(0)
})

it('对话监听器不接管另一对话的选区或正文外编辑器', () => {
  const {scope, listeners, event, data} = fixture()
  scope.contains = () => false
  const cleanup = observeMessageSelectionCopy(scope as unknown as HTMLElement)
  for (const listener of listeners) listener(event as unknown as ClipboardEvent)
  expect(data.has('text/plain')).toBe(false)
  scope.contains = () => true
  event.target = {closest: () => ({})}
  for (const listener of listeners) listener(event as unknown as ClipboardEvent)
  expect(data.has('text/plain')).toBe(false)
  expect(event.preventDefault).not.toHaveBeenCalled()
  cleanup()
})

it('正文已经处理的复制不会被对话监听器再次写入', () => {
  const {scope, listeners, event, copy} = fixture()
  const cleanup = observeMessageSelectionCopy(scope as unknown as HTMLElement)
  copy()
  for (const listener of listeners) listener(event as unknown as ClipboardEvent)
  expect(event.clipboardData.setData).toHaveBeenCalledOnce()
  cleanup()
})

it('没有剪贴板数据的复制事件不清空原有数据', () => {
  const {event, scope} = fixture()
  copyMessageSelection({...event, target: null, clipboardData: null}, scope as unknown as HTMLElement)
  expect(event.clipboardData.clearData).not.toHaveBeenCalled()
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
