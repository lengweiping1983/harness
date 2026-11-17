import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it} from 'vitest'
import {setLocale} from '@/i18n'
import {AcademyStruggle, AcademyStruggleCard} from '@/components/inspector/AcademyStruggle'
import {academyReceiptMatches} from '../academy'
import type {AcademyLesson, AcademyReceipt, AcademyStruggleSession} from '@/types'

afterEach(async () => {await setLocale('zh-CN')})

const lesson: AcademyLesson = {id: 'multiply-meaning', mode: 'study', order: 4, title: 'Multiplication', goal: 'Equal groups',
  reading: 'reading', milestone: false, run_command: [], source_manifest: [], references: [], prerequisites: [], trace_labels: {},
  struggle: {challenge: 'Arrange 12 objects to show 4 × 3.', window_minutes: 5}}
const session = (patch: Partial<AcademyStruggleSession> = {}): AcademyStruggleSession => ({
  id: 'struggle-one', project_id: 'project', lesson_id: 'multiply-meaning', request_id: 'start-request',
  challenge: 'Arrange 12 objects to show 4 × 3.', window_minutes: 5, status: 'struggling', attempt: '',
  started_at: 1, attempt_at: null, revealed_at: null, ...patch,
})
const receipt = (patch: Partial<AcademyReceipt> = {}): AcademyReceipt => ({
  request_id: 'attempt-request', status: 'ready', result: {struggle: session({status: 'attempted', attempt: 'my try', attempt_at: 2})}, finished_at: 3, ...patch,
})
const card = (overrides: Partial<Parameters<typeof AcademyStruggleCard>[0]> = {}) => createElement(AcademyStruggleCard, {
  task: lesson.struggle!, session: null, remaining: 300, draft: '', busy: false,
  onDraft: () => {}, onStart: () => {}, onAttempt: () => {}, onReveal: () => {}, ...overrides,
})

it('matches struggle receipts by the write request, not the frozen start request', () => {
  expect(academyReceiptMatches(receipt(), 'attempt-request', 'project')).toBe(true)
  expect(academyReceiptMatches(receipt(), 'attempt-request', 'other')).toBe(false)
  expect(academyReceiptMatches(receipt(), 'start-request', 'project')).toBe(false)
  expect(academyReceiptMatches(receipt({status: 'unknown', result: {}}), 'attempt-request', 'project')).toBe(true)
  expect(academyReceiptMatches(receipt({status: 'failed', result: {error_code: 'struggle-too-early'}}), 'attempt-request', 'project')).toBe(true)
  expect(academyReceiptMatches(receipt({result: {}}), 'attempt-request', 'project')).toBe(false)
})

it('offers the challenge before a session exists', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(card())
  expect(html).toContain('data-academy-struggle="offer"')
  expect(html).toContain('Arrange 12 objects to show 4 × 3.')
  expect(html).toContain('at least 5 minutes')
  expect(html).toContain('Start struggle')
})

it('keeps reveal locked during the window until an attempt is saved', async () => {
  await setLocale('en')
  const locked = renderToStaticMarkup(card({session: session(), remaining: 240}))
  expect(locked).toContain('data-academy-struggle="struggling"')
  expect(locked).toContain('240s left')
  expect(locked).toContain('disabled=""')
  const ready = renderToStaticMarkup(card({session: session({status: 'attempted', attempt: 'my try', attempt_at: 2}), remaining: 0, draft: 'my try'}))
  expect(ready).toContain('data-academy-struggle="attempted"')
  expect(ready).not.toContain('disabled=""')
})

it('shows the frozen attempt and comparison prompt after reveal', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(card({session: session({status: 'revealed', attempt: 'I made 3 groups of 4.', attempt_at: 2, revealed_at: 3}), remaining: 0}))
  expect(html).toContain('data-academy-struggle="revealed"')
  expect(html).toContain('Compare your attempt with the explanation')
  expect(html).toContain('I made 3 groups of 4.')
  expect(html).not.toContain('Reveal explanation')
})

it('renders nothing for lessons without a struggle task and switches language cleanly', async () => {
  await setLocale('en')
  const plain = {...lesson, struggle: undefined}
  expect(renderToStaticMarkup(createElement(AcademyStruggle, {projectId: 'project', lesson: plain, active: false, disabled: false, onPhase: () => {}}))).toBe('')
  const english = renderToStaticMarkup(card())
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(card())
  expect(english).toContain('Struggle first')
  expect(chinese).toContain('先挣扎一下')
  for (const html of [english, chinese]) expect(html).toContain('Arrange 12 objects to show 4 × 3.')
})
