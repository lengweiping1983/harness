import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it} from 'vitest'
import {setLocale} from '@/i18n'
import {AcademyStepCard, AcademySteps} from '@/components/inspector/AcademySteps'
import {academyReceiptMatches} from '../academy'
import type {AcademyLesson, AcademyReceipt, AcademyStepPublic} from '@/types'

afterEach(async () => {await setLocale('zh-CN')})

const step = (patch: Partial<AcademyStepPublic> = {}): AcademyStepPublic => ({id: 'worked', kind: 'explain', prompt: 'Why must parts be equal?', content: 'Cut a circle into 2 equal parts.', ...patch})
const card = (overrides: Partial<Parameters<typeof AcademyStepCard>[0]> = {}) => createElement(AcademyStepCard, {
  step: step(), busy: false, feedback: null, onSubmit: () => {}, onNext: () => {}, ...overrides,
})
const lesson: AcademyLesson = {id: 'fraction-intuition', mode: 'study', order: 5, title: 'Fractions', goal: 'g', reading: 'r',
  milestone: false, run_command: [], source_manifest: [], references: [], prerequisites: [], trace_labels: {},
  steps: [step(), step({id: 'denominator', kind: 'numeric', prompt: 'Denominator?', content: undefined, tolerance: 0})]}

it('renders each step kind without revealing answers', async () => {
  await setLocale('en')
  const explain = renderToStaticMarkup(card())
  expect(explain).toContain('data-step-kind="explain"')
  expect(explain).toContain('Cut a circle into 2 equal parts.')
  expect(explain).toContain('disabled=""')
  const numeric = renderToStaticMarkup(card({step: step({kind: 'numeric', prompt: 'Denominator?', content: undefined, tolerance: 0})}))
  expect(numeric).toContain('type="number"')
  const choice = renderToStaticMarkup(card({step: step({kind: 'choice', prompt: 'Which half?', content: undefined, options: [{id: 'a', label: 'Unequal'}, {id: 'b', label: 'Equal'}]})}))
  expect(choice).toContain('Unequal')
  expect(choice).not.toContain('answer_id')
  const ordering = renderToStaticMarkup(card({step: step({kind: 'ordering', prompt: 'Order it', content: undefined, items: [{id: 'y', label: 'Second'}, {id: 'x', label: 'First'}]})}))
  expect(ordering).toContain('data-step-kind="ordering"')
  expect(ordering).toContain('Move up')
})

it('shows immediate feedback and gates advancement on correctness', async () => {
  await setLocale('en')
  const correct = renderToStaticMarkup(card({feedback: {correct: true, text: 'Correct!'}}))
  expect(correct).toContain('Correct!')
  expect(correct).toContain('Next step')
  expect(correct).not.toContain('Check this step')
  const wrong = renderToStaticMarkup(card({feedback: {correct: false, text: 'Not yet — try again.'}}))
  expect(wrong).toContain('Not yet — try again.')
  expect(wrong).toContain('Check this step')
  expect(wrong).not.toContain('Next step')
})

it('matches step receipts and renders nothing without steps', async () => {
  const receipt: AcademyReceipt = {request_id: 'r1', status: 'ready', result: {step: {correct: true, completed: false, step: step(), response: {step_id: 'worked', correct: true, response: 'x', attempts: 1, at: 1}}}, finished_at: 2}
  expect(academyReceiptMatches(receipt, 'r1', 'project')).toBe(true)
  expect(academyReceiptMatches(receipt, 'other', 'project')).toBe(false)
  expect(academyReceiptMatches({...receipt, result: {}}, 'r1', 'project')).toBe(false)
  await setLocale('en')
  const plain = {...lesson, steps: undefined}
  expect(renderToStaticMarkup(createElement(AcademySteps, {projectId: 'project', lesson: plain, active: false, disabled: false}))).toBe('')
})

it('switches language without changing step identity', async () => {
  await setLocale('en')
  const english = renderToStaticMarkup(card())
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(card())
  expect(english).toContain('Check this step')
  expect(chinese).toContain('核对这一步')
  for (const html of [english, chinese]) {
    expect(html).toContain('data-academy-step="worked"')
    expect(html).toContain('Why must parts be equal?')
  }
})

it('adds rotating encouragement on correct steps', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(card({feedback: {correct: true, text: 'Right: the denominator counts equal parts.'}, cheer: 'Nice thinking!'}))
  expect(html).toContain('Nice thinking!')
  expect(html).toContain('Right: the denominator counts equal parts.')
  const noCheer = renderToStaticMarkup(card({feedback: {correct: false, text: 'Not yet.'}, cheer: 'Nice thinking!'}))
  expect(noCheer).not.toContain('Nice thinking!')
})
