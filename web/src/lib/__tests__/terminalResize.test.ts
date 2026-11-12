import {afterEach, expect, it, vi} from 'vitest'
import {terminalResizeQueue} from '../terminalResize'
afterEach(() => vi.useRealTimers())
it('隐藏及重新激活后隔离旧尺寸回包，并同步最新尺寸', async () => {
  vi.useFakeTimers()
  let finish!: (error: string | null) => void
  const write = vi.fn().mockImplementationOnce(() => new Promise<string | null>(resolve => { finish = resolve })).mockResolvedValue(null)
  const report = vi.fn()
  const queue = terminalResizeQueue(write, report)
  queue.resize(80, 24)
  queue.setActive(true)
  await vi.advanceTimersByTimeAsync(100)
  queue.setActive(false)
  queue.resize(100, 30)
  queue.setActive(true)
  finish('旧错误')
  await Promise.resolve()
  expect(report).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(100)
  expect(write).toHaveBeenLastCalledWith({cols: 100, rows: 30})
  expect(report).toHaveBeenCalledExactlyOnceWith(null)
  queue.dispose()
})
