import { afterEach, describe, expect, it, vi } from 'vitest'
vi.mock('../http', async importOriginal => ({...await importOriginal<typeof import('../http')>(), runtimeRequest: vi.fn()}))
import { runtimeRequest } from '../http'
import { moveQueuedInput, type QueueEntry } from '../queueEdits'
import { setLocale } from '@/i18n'
const request = vi.mocked(runtimeRequest)
afterEach(() => { request.mockReset(); setLocale('zh-CN') })
describe('queue order receipt recovery', () => {
  const first = {id: 'first', kind: 'runs', edit_version: 'a'} as QueueEntry
  const second = {id: 'second', kind: 'runs', edit_version: 'b'} as QueueEntry
  for (const language of ['en', 'zh-CN'] as const) {
    it(`reports an unconfirmed order in ${language} without replaying the write`, async () => {
      setLocale(language)
      request.mockRejectedValueOnce(new Error('lost response')).mockResolvedValueOnce({runs: [first, second]})
      await expect(moveQueuedInput('session', [first, second], first, 1)).rejects.toThrow(language === 'en' ? 'The queue order could not be confirmed. Refresh the list before trying again.' : '队列顺序尚未确认，请刷新列表后再决定是否重试。')
      expect(request.mock.calls.map(call => call[1])).toEqual(['POST', 'GET'])
    })
  }
})
