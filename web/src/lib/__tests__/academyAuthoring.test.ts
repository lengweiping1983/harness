import {afterEach, expect, it, vi} from 'vitest'
import type {AcademyDesign, AcademyReceipt} from '@/types'
import {academyDesignPendingKey, academyDesignReceiptMatches, academyTopicDraftKey, createAcademyTopicDraftStorage, readAcademyDesignPending, watchAcademyDesigns} from '../academyAuthoring'
import {academyProgressPercent, academyResumeLesson, academyStages} from '../academy'
import type {ApiResult} from '../http'

const design = (patch: Partial<AcademyDesign> = {}): AcademyDesign => ({id: 'design_one', request_id: 'request-one',
  topic: 'Learn a topic', background: 'My background 原文', course_id: 'personal_one', language: 'en',
  model: 'fixture-model', created_at: 1, status: 'generating', phase: 'planning', review_notes: [], ...patch})
const response = (designs: AcademyDesign[]): ApiResult<{designs: AcademyDesign[]}> => ({ok: true, data: {designs}})
function storage(): Storage {
  const rows = new Map<string, string>()
  return {get length() {return rows.size}, clear: () => rows.clear(), key: index => [...rows.keys()][index] ?? null,
    getItem: key => rows.get(key) ?? null, setItem: (key, value) => {rows.set(key, value)}, removeItem: key => {rows.delete(key)}}
}
afterEach(() => {vi.useRealTimers(); vi.restoreAllMocks()})

it('keeps topic drafts in their original language and refuses to overwrite corrupted storage', () => {
  const browser = storage(), drafts = createAcademyTopicDraftStorage(() => browser)
  const original = {topic: '学习一个主题', background: '少量基础 原文'}
  expect(drafts.write(original)).toBe(true)
  expect(drafts.read()).toEqual({draft: original, failed: false})
  browser.setItem(academyTopicDraftKey, '{bad json')
  expect(drafts.read()).toEqual({draft: original, failed: true})
  const edited = {topic: 'My next topic', background: 'New input'}
  expect(drafts.write(edited)).toBe(false)
  expect(browser.getItem(academyTopicDraftKey)).toBe('{bad json')
  expect(drafts.read().draft).toEqual(edited)
})

it('does not let a later read of older storage replace an unpersisted topic edit', () => {
  const browser = storage(), drafts = createAcademyTopicDraftStorage(() => browser)
  drafts.write({topic: 'old', background: ''})
  const save = vi.spyOn(browser, 'setItem').mockImplementation(() => {throw Error('quota')})
  expect(drafts.write({topic: 'new', background: 'preserved'})).toBe(false)
  save.mockRestore()
  expect(drafts.read()).toEqual({draft: {topic: 'new', background: 'preserved'}, failed: true})
  expect(drafts.write({topic: 'new', background: 'preserved'})).toBe(true)
  expect(drafts.read().failed).toBe(false)
})

it('requires a valid persisted request identity and preserves malformed bytes', () => {
  const browser = storage()
  const pending = {requestId: 'request-one', topic: 'Learn a topic', background: ''}
  expect(readAcademyDesignPending(browser)).toBeNull()
  browser.setItem(academyDesignPendingKey, JSON.stringify(pending))
  expect(readAcademyDesignPending(browser)).toEqual(pending)
  for (const raw of ['null', '[]', '{}', JSON.stringify({...pending, topic: '  '}), JSON.stringify({...pending, requestId: '../outside'}), JSON.stringify({...pending, extra: true})]) {
    browser.setItem(academyDesignPendingKey, raw)
    expect(() => readAcademyDesignPending(browser)).toThrow()
    expect(browser.getItem(academyDesignPendingKey)).toBe(raw)
  }
})

it('confirms only the exact course design request and submitted topic snapshot', () => {
  const pending = {requestId: 'request-one', topic: 'Learn a topic', background: 'My background 原文'}
  const receipt: AcademyReceipt = {request_id: pending.requestId, status: 'ready', result: {design: design()}}
  expect(academyDesignReceiptMatches(receipt, pending)).toBe(true)
  expect(academyDesignReceiptMatches({...receipt, request_id: 'other'}, pending)).toBe(false)
  expect(academyDesignReceiptMatches({...receipt, result: {design: design({request_id: 'other'})}}, pending)).toBe(false)
  expect(academyDesignReceiptMatches({...receipt, result: {design: design({topic: 'A different topic'})}}, pending)).toBe(false)
  expect(academyDesignReceiptMatches({...receipt, result: {design: design({background: 'Changed'})}}, pending)).toBe(false)
  expect(academyDesignReceiptMatches({request_id: pending.requestId, status: 'unknown', result: {}}, pending)).toBe(true)
})

it('polls only while generation is pending and never overlaps a slow observation', async () => {
  vi.useFakeTimers()
  let resolve!: (result: ApiResult<{designs: AcademyDesign[]}>) => void
  const read = vi.fn().mockImplementationOnce(() => new Promise(done => {resolve = done})).mockResolvedValue(response([design({status: 'ready'})]))
  const publish = vi.fn(), failure = vi.fn()
  const stop = watchAcademyDesigns({read, publish, failure})
  await vi.advanceTimersByTimeAsync(8000)
  expect(read).toHaveBeenCalledTimes(1)
  resolve(response([design()]))
  await vi.advanceTimersByTimeAsync(2000)
  expect(read).toHaveBeenCalledTimes(2)
  expect(publish).toHaveBeenLastCalledWith([design({status: 'ready'})])
  await vi.advanceTimersByTimeAsync(8000)
  expect(read).toHaveBeenCalledTimes(2)
  expect(failure).not.toHaveBeenCalled()
  stop()
})

it('aborts hidden observations and rejects a late result even when the transport ignores abort', async () => {
  vi.useFakeTimers()
  let resolve!: (result: ApiResult<{designs: AcademyDesign[]}>) => void
  let signal: AbortSignal | undefined
  const read = vi.fn(value => {signal = value; return new Promise<ApiResult<{designs: AcademyDesign[]}>>(done => {resolve = done})})
  const publish = vi.fn(), failure = vi.fn()
  const stop = watchAcademyDesigns({read, publish, failure, pending: true})
  stop()
  expect(signal?.aborted).toBe(true)
  resolve(response([design({status: 'ready'})]))
  await vi.advanceTimersByTimeAsync(10000)
  expect(publish).not.toHaveBeenCalled()
  expect(failure).not.toHaveBeenCalled()
  expect(read).toHaveBeenCalledTimes(1)
})

it('does not poll unknown results or repeatedly denied observations, and rejects duplicate identities', async () => {
  vi.useFakeTimers()
  for (const result of [response([design({status: 'unknown'})]), {ok: false as const, status: 403, message: 'denied', code: 'denied'}, response([design(), design()])]) {
    const read = vi.fn().mockResolvedValue(result), failure = vi.fn(), publish = vi.fn()
    const stop = watchAcademyDesigns({read, failure, publish, pending: true})
    await vi.advanceTimersByTimeAsync(10000)
    expect(read).toHaveBeenCalledTimes(1)
    if (result.ok && result.data.designs.length > 1) {
      expect(publish).not.toHaveBeenCalled()
      expect(failure).toHaveBeenCalledWith(expect.objectContaining({code: 'response/invalid-schema'}))
    }
    stop()
  }
})

it('counts study activity completion separately from code checks and resumes the next unfinished lesson', () => {
  const lesson = {id: 'study', mode: 'study' as const, order: 1, title: 'Study', goal: 'Explain', reading: '', milestone: false,
    run_command: [], source_manifest: [], references: [], prerequisites: [], trace_labels: {}, stage: 'Start', stage_goal: 'Use a new example'}
  const course = {id: 'personal', origin: 'personal' as const, version: '1', title: 'Course', description: '', source_revision: '1', references: [],
    lessons: [lesson, {...lesson, id: 'next', order: 2}]}
  const project = {id: 'project', course_id: 'personal', course_version: '1', name: '', revision: 1, active_lesson_id: 'study', course_fingerprint: 'a'.repeat(64),
    fingerprint: 'b'.repeat(64), created_at: 1, root: '/fixture', sandbox: 'unavailable' as const,
    progress: [{lesson_id: 'study', state: 'completed' as const, checkpoint_id: 'checkpoint', evidence_ids: ['study-evidence'], attempt_count: 1}]}
  expect(academyProgressPercent(course, project)).toBe(50)
  expect(academyResumeLesson(course, project)?.id).toBe('next')
  expect(academyStages(course)[0].goal).toBe('Use a new example')
  expect(project.progress[0].state).toBe('completed')
})
