import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it} from 'vitest'
import {setLocale} from '@/i18n'
import {AcademyQuizResult, AcademyTeach} from '@/components/inspector/AcademyTeach'
import {academyReceiptMatches, academyTeachMisconception, academyTeachTurns} from '../academy'
import type {AcademyLesson, AcademyReceipt, AcademyTeachQuiz, AcademyTutorTurn} from '@/types'

afterEach(async () => {await setLocale('zh-CN')})

const lesson: AcademyLesson = {id: 'fractions', mode: 'study', order: 1, title: 'Fractions', goal: 'Compare fractions',
  reading: 'reading', milestone: false, run_command: [], source_manifest: [], references: [], prerequisites: [], trace_labels: {},
  knowledge_checks: [{id: 'q1', prompt: 'Which is larger?', answer_id: 'right', hints: ['think'],
    options: [{id: 'right', label: 'One half', feedback: 'Yes'}, {id: 'wrong', label: 'One third', feedback: 'No'}]}]}
const turn = (patch: Partial<AcademyTutorTurn> = {}): AcademyTutorTurn => ({
  id: 'tutor-one', project_id: 'project', lesson_id: 'fractions', request_id: 'request-one',
  input_fingerprint: 'a'.repeat(64), prompt: 'Half means one of two equal parts.', allow_execution: false, require_execution: false,
  mode: 'teach', misconception: {check_id: 'q1', option_id: 'wrong', prompt: 'Which is larger?', label: 'One third', feedback: 'No'},
  response: 'But 3 is bigger than 2?', model: 'teach-model', language: 'en', status: 'completed', actions: [], created_at: 1, ...patch,
})
const quiz = (patch: Partial<AcademyTeachQuiz> = {}): AcademyTeachQuiz => ({
  id: 'teachquiz-one', project_id: 'project', lesson_id: 'fractions', turn_id: 'tutor-one', request_id: 'quiz-request',
  status: 'completed', answers: [{check_id: 'q1', option_id: 'right'}], results: [{check_id: 'q1', correct: true}],
  correct: true, model: 'quiz-model', language: 'en', created_at: 2, ...patch,
})
const receipt = (patch: Partial<AcademyReceipt> = {}): AcademyReceipt => ({
  request_id: 'quiz-request', status: 'ready', result: {quiz: quiz()}, finished_at: 3, ...patch,
})

it('binds quiz receipts to the original request and project', () => {
  expect(academyReceiptMatches(receipt(), 'quiz-request', 'project')).toBe(true)
  expect(academyReceiptMatches(receipt(), 'quiz-request', 'other')).toBe(false)
  expect(academyReceiptMatches(receipt(), 'other', 'project')).toBe(false)
  expect(academyReceiptMatches(receipt({result: {quiz: quiz({request_id: 'other'})}}), 'quiz-request', 'project')).toBe(false)
  expect(academyReceiptMatches(receipt({status: 'unknown', result: {}}), 'quiz-request', 'project')).toBe(true)
  expect(academyReceiptMatches(receipt({status: 'failed', result: {error_code: 'quiz-invalid'}}), 'quiz-request', 'project')).toBe(true)
  expect(academyReceiptMatches(receipt({result: {}}), 'quiz-request', 'project')).toBe(false)
})

it('previews the same misconception the host freezes', () => {
  expect(academyTeachMisconception(lesson)).toEqual({check_id: 'q1', option_id: 'wrong', prompt: 'Which is larger?', label: 'One third', feedback: 'No'})
  const second = {...lesson, knowledge_checks: [...lesson.knowledge_checks!, {id: 'q2', prompt: 'Again?', answer_id: 'a', hints: [],
    options: [{id: 'a', label: 'A', feedback: 'ok'}, {id: 'b', label: 'B', feedback: 'no'}]}]}
  expect(academyTeachMisconception(second)?.check_id).toBe('q1')
  expect(academyTeachMisconception({...lesson, knowledge_checks: []})).toBeUndefined()
})

it('lists only this lesson teaching conversations in time order', () => {
  const rows = [turn({id: 'late', created_at: 5}), turn({id: 'tutor-mode', mode: 'tutor', created_at: 2}),
    turn({id: 'other-lesson', lesson_id: 'decimals', created_at: 3}), turn({id: 'early', created_at: 1})]
  expect(academyTeachTurns(rows, 'fractions').map(item => item.id)).toEqual(['early', 'late'])
})

it('renders the teach entry with misconception preview and no fetching when inactive', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(createElement(AcademyTeach, {projectId: 'project', lesson, fingerprint: 'a'.repeat(64), turns: [], disabled: false, active: false, onRefresh: () => {}}))
  expect(html).toContain('data-academy-section="teach"')
  expect(html).toContain('You be the teacher (teach the AI student)')
  expect(html).toContain('The AI student&#x27;s current wrong idea')
  expect(html).toContain('One third')
  expect(html).toContain('Start by explaining the lesson goal')
  expect(html).not.toContain('role="status"')
})

it('renders the conversation and offers the quiz only after a completed teach turn', async () => {
  await setLocale('en')
  const base = {projectId: 'project', lesson, fingerprint: 'a'.repeat(64), disabled: false, active: false, onRefresh: () => {}}
  const running = renderToStaticMarkup(createElement(AcademyTeach, {...base, turns: [turn({status: 'running', response: ''})]}))
  expect(running).toContain('Half means one of two equal parts.')
  expect(running).not.toContain('Give the student a quiz')
  const completed = renderToStaticMarkup(createElement(AcademyTeach, {...base, turns: [turn()]}))
  expect(completed).toContain('data-teach-turn-id="tutor-one"')
  expect(completed).toContain('But 3 is bigger than 2?')
  expect(completed).toContain('Give the student a quiz')
})

it('renders quiz outcomes without revealing the correct option', async () => {
  await setLocale('en')
  const correct = renderToStaticMarkup(createElement(AcademyQuizResult, {quiz: quiz()}))
  expect(correct).toContain('your teaching worked')
  expect(correct).toContain('Question 1')
  expect(correct).not.toContain('One half')
  const wrong = renderToStaticMarkup(createElement(AcademyQuizResult, {quiz: quiz({correct: false, results: [{check_id: 'q1', correct: false}]})}))
  expect(wrong).toContain('kept the misconception')
  const running = renderToStaticMarkup(createElement(AcademyQuizResult, {quiz: quiz({status: 'running', results: [], answers: []})}))
  expect(running).toContain('taking the quiz')
  const unknown = renderToStaticMarkup(createElement(AcademyQuizResult, {quiz: quiz({status: 'unknown', error_code: 'quiz-interrupted'})}))
  expect(unknown).toContain('unconfirmed')
})

it('switches language without rewriting conversation identities', async () => {
  await setLocale('en')
  const base = {projectId: 'project', lesson, fingerprint: 'a'.repeat(64), turns: [turn()], disabled: false, active: false, onRefresh: () => {}}
  const english = renderToStaticMarkup(createElement(AcademyTeach, base))
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(createElement(AcademyTeach, base))
  expect(english).toContain('AI student')
  expect(chinese).toContain('AI 学生')
  for (const html of [english, chinese]) {
    expect(html).toContain('data-teach-turn-id="tutor-one"')
    expect(html).toContain('Half means one of two equal parts.')
  }
})
