import { describe, expect, it, vi } from 'vitest'
import type { KeyboardEvent } from 'react'
import { handlePageNavigationKeyDown, handleTablistKeyDown } from '../tabNavigation'

function fixture(key: string, index = 1, extra = {}) {
  const tabs = Array.from({ length: 3 }, () => ({ click: vi.fn(), focus: vi.fn(), closest: () => null }))
  const event = { key, target: tabs[index], currentTarget: { querySelectorAll: () => tabs },
    nativeEvent: { isComposing: false, keyCode: 0 }, preventDefault: vi.fn(), ...extra }
  handleTablistKeyDown(event as unknown as KeyboardEvent<HTMLElement>)
  return { tabs, event }
}

describe('tab keyboard navigation', () => {
  for (const [key, start, target] of [['ArrowRight', 2, 0], ['ArrowLeft', 0, 2],
    ['Home', 1, 0], ['End', 1, 2]] as const) {
    it(`${key} selects and focuses its destination`, () => {
      const { tabs, event } = fixture(key, start)
      expect(event.preventDefault).toHaveBeenCalledOnce()
      tabs.forEach((tab, index) => {
        expect(tab.click).toHaveBeenCalledTimes(index === target ? 1 : 0)
        expect(tab.focus).toHaveBeenCalledTimes(index === target ? 1 : 0)
      })
    })
  }
  it('leaves modified keys, IME and previously handled input alone', () => {
    for (const extra of [{ ctrlKey: true }, { altKey: true }, { metaKey: true }, { shiftKey: true },
      { defaultPrevented: true }, { nativeEvent: { isComposing: true, keyCode: 0 } },
      { nativeEvent: { isComposing: false, keyCode: 229 } }]) {
      const { tabs, event } = fixture('ArrowRight', 1, extra)
      expect(event.preventDefault).not.toHaveBeenCalled()
      tabs.forEach(tab => expect(tab.click).not.toHaveBeenCalled())
    }
  })
  it('skips tabs within collapsed navigation groups', () => {
    const tabs = Array.from({length: 3}, (_, index) => ({click: vi.fn(), focus: vi.fn(), closest: () => index === 1 ? {} : null}))
    handleTablistKeyDown({key: 'ArrowRight', target: tabs[0], currentTarget: {querySelectorAll: () => tabs},
      nativeEvent: {isComposing: false}, preventDefault: vi.fn()} as unknown as KeyboardEvent<HTMLElement>)
    expect(tabs[1].click).not.toHaveBeenCalled()
    expect(tabs[2].click).toHaveBeenCalledOnce()
  })
  it('does not consume unrelated keys or events outside a tab button', () => {
    for (const { event } of [fixture('Enter'), fixture('ArrowRight', 1, { target: {} })]) {
      expect(event.preventDefault).not.toHaveBeenCalled()
    }
  })
  it('page navigation selects pages without including disclosure buttons', () => {
    const pages = Array.from({length: 2}, () => ({click: vi.fn(), focus: vi.fn(), closest: () => null}))
    const query = vi.fn(() => pages)
    handlePageNavigationKeyDown({key: 'ArrowDown', target: pages[1], currentTarget: {querySelectorAll: query},
      nativeEvent: {isComposing: false}, preventDefault: vi.fn()} as unknown as KeyboardEvent<HTMLElement>)
    expect(query).toHaveBeenCalledWith('button[data-page][aria-controls]:not(:disabled)')
    expect(pages[0].click).toHaveBeenCalledOnce()
    expect(pages[1].click).not.toHaveBeenCalled()
  })
})
