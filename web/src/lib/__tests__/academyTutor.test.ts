import {afterEach, expect, it, vi} from 'vitest'
import type {AcademyReceipt, AcademyTutorTurn} from '@/types'
import {academyApi, academyReceiptMatches, watchAcademyTutorTurns} from '../academy'

const turn: AcademyTutorTurn = {id: 'tutor-one', request_id: 'request-one', project_id: 'project-one', lesson_id: 'lesson-one',
  input_fingerprint: 'a'.repeat(64), prompt: '用户问题 {value}', allow_execution: true, require_execution: false, response: '', model: 'model', language: 'en',
  status: 'running', actions: [], created_at: 1}
afterEach(() => {vi.useRealTimers(); vi.unstubAllGlobals()})

it('reads optional original draft text through the generated HTTP contract without a write', async () => {
  const fetch = vi.fn<typeof globalThis.fetch>()
  vi.stubGlobal('fetch', fetch)
  const snapshot = {tutor_turns: [{...turn, partial_response: '正在生成 / in progress'}]}
  fetch.mockResolvedValueOnce(new Response(JSON.stringify(snapshot)))
  expect(await academyApi.tutorTurns('project-one')).toEqual({ok: true, data: snapshot})
  fetch.mockResolvedValueOnce(new Response(JSON.stringify({tutor_turns: [turn]})))
  expect(await academyApi.tutorTurns('project-one')).toEqual({ok: true, data: {tutor_turns: [turn]}})
  fetch.mockResolvedValueOnce(new Response(JSON.stringify({tutor_turns: [{...turn, partial_response: 7}]})))
  expect(await academyApi.tutorTurns('project-one')).toMatchObject({ok: false, code: 'response/invalid-schema'})
  expect(fetch).toHaveBeenCalledTimes(3)
  expect(fetch.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true)
})

it('matches tutor acceptance only to its original request and project', () => {
  const receipt: AcademyReceipt = {request_id: 'request-one', status: 'ready', result: {tutor_turn: turn}}
  expect(academyReceiptMatches(receipt, 'request-one', 'project-one')).toBe(true)
  expect(academyReceiptMatches(receipt, 'request-one', 'other-project')).toBe(false)
  expect(academyReceiptMatches({...receipt, result: {tutor_turn: {...turn, request_id: 'other-request'}}}, 'request-one', 'project-one')).toBe(false)
})

it('carries the selected self-check through the native request and generated receipt without source writes', async () => {
  const selection = {check_id: 'check-one', option_id: 'wrong-choice'}
  const selected = {...turn, allow_execution: false, knowledge_check: selection}
  const receipt: AcademyReceipt = {request_id: turn.request_id, status: 'ready', result: {tutor_turn: selected}}
  const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValueOnce(Response.json(receipt))
    .mockResolvedValueOnce(Response.json({tutor_turns: [selected]}))
    .mockResolvedValueOnce(Response.json({tutor_turns: [{...selected, knowledge_check: {...selection, option_id: 7}}]}))
  vi.stubGlobal('fetch', fetch)
  const body = {request_id: turn.request_id, lesson_id: turn.lesson_id, expected_fingerprint: turn.input_fingerprint,
    prompt: 'Help me reason about my choice', allow_execution: false, require_execution: false, knowledge_check: selection}
  expect(await academyApi.tutor(turn.project_id, body)).toEqual({ok: true, data: receipt})
  expect(JSON.parse(fetch.mock.calls[0][1]!.body as string)).toEqual(body)
  expect(await academyApi.tutorTurns(turn.project_id)).toEqual({ok: true, data: {tutor_turns: [selected]}})
  expect(await academyApi.tutorTurns(turn.project_id)).toMatchObject({ok: false, code: 'response/invalid-schema'})
})

it('keeps a stop receipt request separate from the original tutor request', () => {
  const receipt: AcademyReceipt = {request_id: 'stop-one', status: 'ready', result: {tutor_turn: {...turn, stopping: true}}}
  expect(academyReceiptMatches(receipt, 'stop-one', 'project-one')).toBe(false)
  expect(academyReceiptMatches(receipt, 'stop-one', 'project-one', 'request-one')).toBe(true)
  expect(academyReceiptMatches(receipt, 'different-stop', 'project-one', 'request-one')).toBe(false)
  expect(academyReceiptMatches(receipt, 'stop-one', 'other-project', 'request-one')).toBe(false)
})

it('owns a single pending read and ignores a late response after observation ends', async () => {
  vi.useFakeTimers()
  let release!: (result: {ok: true; data: {tutor_turns: AcademyTutorTurn[]}}) => void
  let signal: AbortSignal | undefined
  const read = vi.fn((_project: string, value?: AbortSignal) => {signal = value; return new Promise<{ok: true; data: {tutor_turns: AcademyTutorTurn[]}}>(resolve => {release = resolve})})
  const publish = vi.fn(), failure = vi.fn()
  const stop = watchAcademyTutorTurns('project-one', {read, publish, failure, pending: true, interval: 10})
  await vi.advanceTimersByTimeAsync(100)
  expect(read).toHaveBeenCalledTimes(1)
  stop()
  expect(signal?.aborted).toBe(true)
  release({ok: true, data: {tutor_turns: [{...turn, status: 'completed', response: '原文'}]}})
  await vi.advanceTimersByTimeAsync(100)
  expect(publish).not.toHaveBeenCalled()
  expect(failure).not.toHaveBeenCalled()
  expect(read).toHaveBeenCalledTimes(1)
})

it('rejects a foreign project without publishing or continuing the poll', async () => {
  vi.useFakeTimers()
  const read = vi.fn(async () => ({ok: true as const, data: {tutor_turns: [{...turn, project_id: 'other'}]}}))
  const publish = vi.fn(), failure = vi.fn()
  const stop = watchAcademyTutorTurns('project-one', {read, publish, failure, pending: true, interval: 10})
  await vi.advanceTimersByTimeAsync(100)
  expect(publish).not.toHaveBeenCalled()
  expect(failure.mock.calls[0][0].copy).toEqual({key: 'ui.http.invalidResponse'})
  expect(read).toHaveBeenCalledTimes(1)
  stop()
})
