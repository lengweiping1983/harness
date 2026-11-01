import {expect, it, vi} from 'vitest'
import {createBrowserDraftStorage} from '../browserDraftStorage'

it('keeps an unsaved edit and deletion instead of resurrecting the disk value', () => {
  let raw: string | null = 'valid:original'
  let writable = false
  const storage = {length:0, clear:vi.fn(), key:()=>null, getItem: () => raw, setItem: (_key: string, value: string) => {
    if (!writable) throw Error('Original write failure')
    raw = value
  }, removeItem: () => {if (!writable) throw Error('Original remove failure'); raw = null}} satisfies Storage
  const failed = vi.fn()
  const adapter = createBrowserDraftStorage(() => storage, failed, value => value.startsWith('valid:'))
  expect(adapter.getItem('draft')).toBe(raw)
  adapter.setItem('draft', 'valid:new')
  expect(adapter.getItem('draft')).toBe('valid:new')
  expect(raw).toBe('valid:original')
  writable = true
  adapter.setItem('draft', 'valid:new')
  expect(raw).toBe('valid:new')
  writable = false
  adapter.removeItem('draft')
  expect(adapter.getItem('draft')).toBeNull()
  expect(raw).toBe('valid:new')
  expect(failed).toHaveBeenCalledTimes(2)
})
it('rechecks later corruption before writing or deleting and preserves the last loaded value on read failure', () => {
  let raw: string | null = 'valid:original'
  let readable = true
  const storage = {length:0, clear:vi.fn(), key:()=>null, getItem: () => {if (!readable) throw Error('Original read failure'); return raw},
    setItem: vi.fn((_key: string, value: string) => {raw = value}), removeItem: vi.fn(() => {raw = null})} satisfies Storage
  const adapter = createBrowserDraftStorage(() => storage, vi.fn(), value => value.startsWith('valid:'))
  expect(adapter.getItem('draft')).toBe('valid:original')
  readable = false
  expect(adapter.getItem('draft')).toBe('valid:original')
  readable = true; raw = 'corrupt original bytes'
  adapter.setItem('draft', 'valid:new')
  expect(raw).toBe('corrupt original bytes')
  expect(adapter.getItem('draft')).toBe('valid:new')
  adapter.removeItem('draft')
  expect(raw).toBe('corrupt original bytes')
  expect(storage.setItem).not.toHaveBeenCalled()
  expect(storage.removeItem).not.toHaveBeenCalled()
})
