import {expect, it, vi} from 'vitest'
import {createCompletionObserver} from '../completionObserver'
import type {SessionActivity, SessionCompletion} from '../protocol.generated'
const completion = (seq: number, session_id = 'background'): SessionCompletion => ({seq, session_id, run_id: 'run-' + seq, status: 'completed'})
const snapshot = (completion_cursor: number, completions: SessionCompletion[] = [], server_id = 'server'): SessionActivity =>
  ({server_id, completion_cursor, completions, completions_lost: false, catalog_revision: 0, running: []})
it('establishes a baseline, catches short runs, deduplicates live/poll delivery and resumes after outages', () => {
  const deliver = vi.fn(), observer = createCompletionObserver(deliver, vi.fn())
  observer.receive(snapshot(1, [completion(1)])); expect(deliver).not.toHaveBeenCalled()
  observer.live(completion(2, 'current'))
  observer.receive(snapshot(3, [completion(2, 'current'), completion(3)]))
  observer.receive(snapshot(3, [completion(2, 'current'), completion(3)]))
  expect(deliver.mock.calls.map(([row]) => row.run_id)).toEqual(['run-2', 'run-3'])
  expect(observer.cursor()).toEqual({serverId: 'server', after: 3})
  observer.receive(snapshot(5, [completion(4), completion(5)]))
  expect(deliver).toHaveBeenCalledTimes(4)
  observer.receive(snapshot(1, [completion(1)], 'restarted'))
  expect(deliver).toHaveBeenCalledTimes(4)
  observer.receive(snapshot(2, [{...completion(2), run_id: 'new-run'}], 'restarted'))
  expect(deliver).toHaveBeenCalledTimes(5)
})
it('reports corrupt ordering as a gap, ignores older responses and resumes from the new cursor', () => {
  const deliver = vi.fn(), gap = vi.fn(), observer = createCompletionObserver(deliver, gap)
  observer.receive(snapshot(2))
  observer.receive(snapshot(4, [completion(4), completion(3)]))
  expect(observer.cursor()?.after).toBe(4)
  expect(gap).toHaveBeenCalledOnce()
  expect(deliver).not.toHaveBeenCalled()
  observer.receive(snapshot(1, [completion(1)]))
  expect(deliver).not.toHaveBeenCalled()
  observer.receive({...snapshot(5, [completion(5)]), completions_lost: true})
  expect(gap).toHaveBeenCalledTimes(2)
  expect(deliver).toHaveBeenCalledOnce()
})
