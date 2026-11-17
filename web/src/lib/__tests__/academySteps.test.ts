import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it} from 'vitest'
import {setLocale} from '@/i18n'
import {AcademyNumberLine, AcademyStepCard, AcademySteps} from '@/components/inspector/AcademySteps'
import {academyNextStep, academyReceiptMatches} from '../academy'
import type {AcademyLesson, AcademyReceipt, AcademyStep} from '@/types'

afterEach(async () => {await setLocale('zh-CN')})

const step = (patch: Partial<AcademyStep> = {}): AcademyStep => ({id: 'worked', kind: 'explain', prompt: 'Why must parts be equal?', feedback: 'Because equal parts share one name.', content: 'Cut a circle into 2 equal parts.', ...patch})
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
  const choice = renderToStaticMarkup(card({step: step({kind: 'choice', prompt: 'Which half?', content: undefined, options: [{id: 'a', label: 'Unequal', feedback: 'Unequal parts cannot be a half.'}, {id: 'b', label: 'Equal', feedback: 'One of two equal parts.'}]})}))
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

it('renders matching pairs without revealing the answer key', async () => {
  await setLocale('en')
  const matching = step({kind: 'matching', prompt: 'Match each expression to its value.', content: undefined,
    left: [{id: 'e1', label: '2x + 5'}, {id: 'e2', label: '3x'}], right: [{id: 'v9', label: '9'}, {id: 'v11', label: '11'}]})
  const html = renderToStaticMarkup(card({step: matching}))
  expect(html).toContain('data-step-kind="matching"')
  expect(html).toContain('2x + 5')
  expect(html).toContain('<select')
  expect(html).toContain('11')
  expect(html).not.toContain('answer_id')
  expect(html).not.toContain('data-answer')
  expect(html).toContain('disabled=""')
})

it('labels transfer challenges without revealing the answer key', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(card({step: step({kind: 'choice', prompt: 'New situation?', content: undefined, transfer: true, options: [{id: 'a', label: '6 + 4', feedback: 'Combines.'}, {id: 'b', label: '6 - 4', feedback: 'Removes.'}]})}))
  expect(html).toContain('Transfer challenge')
  expect(html).not.toContain('answer_id')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(card({step: step({kind: 'choice', prompt: '新情境？', content: undefined, transfer: true, options: [{id: 'a', label: '6 + 4', feedback: '合起来。'}, {id: 'b', label: '6 - 4', feedback: '去掉。'}]})}))
  expect(chinese).toContain('迁移挑战')
})
it('requires a confidence rating before submitting and renders calibration feedback bilingually', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(card({step: step({kind: 'numeric', prompt: 'Denominator?', content: undefined, tolerance: 0})}))
  expect(html).toContain('data-confidence="sure"')
  expect(html).toContain('Before you answer: how sure are you?')
  expect(html).toContain('disabled=""')
  const blind = renderToStaticMarkup(card({feedback: {correct: false, text: 'Not yet.', calibration: 'blindspot'}}))
  expect(blind).toContain('Sure but wrong - this is exactly the gap to repair now.')
  const calibrated = renderToStaticMarkup(card({feedback: {correct: true, text: 'Correct!', calibration: 'calibrated'}}))
  expect(calibrated).toContain('Sure and correct - that is real mastery.')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(card({feedback: {correct: false, text: '再试试', calibration: 'blindspot'}}))
  expect(chinese).toContain('很确定却答错——这正是现在要修复的缺口。')
  expect(chinese).toContain('作答前：你有多确定？')
})
it('shows a contingent scaffold only after a wrong answer', async () => {
  await setLocale('en')
  const wrong = renderToStaticMarkup(card({feedback: {correct: false, text: 'Not yet.', scaffold: 'Count all the equal parts first.'}}))
  expect(wrong).toContain('Count all the equal parts first.')
  expect(wrong).toContain('scaffold')
  const correct = renderToStaticMarkup(card({feedback: {correct: true, text: 'Right!', scaffold: 'Count all the equal parts first.'}}))
  expect(correct).not.toContain('Count all the equal parts first.')
})

it('renders a number line mark for numeric feedback without revealing the answer', async () => {
  await setLocale('en')
  const line = renderToStaticMarkup(createElement(AcademyNumberLine, {min: 0, max: 12, value: 7}))
  expect(line).toContain('data-academy-number-line')
  expect(line).toContain('58.3333')
  const html = renderToStaticMarkup(card({step: step({kind: 'numeric', prompt: 'Denominator?', content: undefined, tolerance: 0}), feedback: {correct: false, text: 'Not yet.', numberLine: {min: 0, max: 12, value: 7}}}))
  expect(html).toContain('data-academy-number-line')
  expect(html).not.toContain('answer_id')
})

it('routes adaptively to a support step on wrong and back after mastery', () => {
  const steps = [
    step({id: 'worked', kind: 'explain'}),
    step({id: 'denominator', kind: 'numeric', prompt: 'Denominator?', content: undefined, tolerance: 0, on_wrong: 'support'}),
    step({id: 'support', kind: 'numeric', prompt: 'Count parts', content: undefined, tolerance: 0}),
    step({id: 'which-half', kind: 'choice', prompt: 'Which?', content: undefined, options: [{id: 'b', label: 'Equal', feedback: 'One of two equal parts.'}]}),
  ]
  expect(academyNextStep(steps, [])?.id).toBe('worked')
  expect(academyNextStep(steps, [{step_id: 'worked', correct: true}])?.id).toBe('denominator')
  expect(academyNextStep(steps, [{step_id: 'worked', correct: true}, {step_id: 'denominator', correct: false}])?.id).toBe('support')
  expect(academyNextStep(steps, [{step_id: 'worked', correct: true}, {step_id: 'denominator', correct: false}, {step_id: 'support', correct: true}])?.id).toBe('denominator')
  expect(academyNextStep(steps, [{step_id: 'worked', correct: true}, {step_id: 'denominator', correct: true}, {step_id: 'which-half', correct: true}])).toBeNull()
})
