import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it} from 'vitest'
import {setLocale} from '@/i18n'
import {AcademyWorld, AcademyWorldCard, AcademyWorldState} from '@/components/inspector/AcademyWorld'
import {academyReceiptMatches, academyWorldParams} from '../academy'
import type {AcademyLesson, AcademyReceipt, AcademyWorldSession} from '@/types'

afterEach(async () => {await setLocale('zh-CN')})
it('renders the float-sink micro-world state bilingually with submerged position', async () => {
  await setLocale('en')
  const floats = renderToStaticMarkup(createElement(AcademyWorldState, {kind: 'float-sink', state: {object: 6, liquid: 10, floats: true, submerged: 0.6, label: 'object 6 / liquid 10 - submerged 60%'}}))
  expect(floats).toContain('data-world-state="float-sink"')
  expect(floats).toContain('Floats')
  expect(floats).toContain('top:22%')
  const sinks = renderToStaticMarkup(createElement(AcademyWorldState, {kind: 'float-sink', state: {object: 12, liquid: 10, floats: false, submerged: 1, label: 'object 12 / liquid 10 - submerged 100%'}}))
  expect(sinks).toContain('Sinks')
  expect(sinks).toContain('top:80%')
  expect(academyWorldParams['float-sink']).toEqual([{key: 'object', min: 1, max: 20}, {key: 'liquid', min: 1, max: 20}])
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(createElement(AcademyWorldState, {kind: 'float-sink', state: {object: 6, liquid: 10, floats: true, submerged: 0.6, label: '物体 6 ／ 液体 10 · 浸入 60%'}}))
  expect(chinese).toContain('漂浮')
})

const lesson: AcademyLesson = {id: 'fraction-intuition', mode: 'study', order: 5, title: 'Fractions', goal: 'Equal parts',
  reading: 'reading', milestone: false, run_command: [], source_manifest: [], references: [], prerequisites: [], trace_labels: {},
  world: {kind: 'fraction-bar'}}
const session = (patch: Partial<AcademyWorldSession> = {}): AcademyWorldSession => ({
  id: 'world-one', project_id: 'project', lesson_id: 'fraction-intuition', request_id: 'start-request', kind: 'fraction-bar',
  params: {parts: 4, shaded: 1}, challenge: {prompt: 'Set 6 equal parts and shade 2. What fraction?', options: [{id: 'option-0', label: '2/6'}, {id: 'option-1', label: '6/2'}], target: {parts: 6, shaded: 2}},
  status: 'predicting', observations: [], note: '', created_at: 1, completed_at: null, ...patch,
})
const receipt = (patch: Partial<AcademyReceipt> = {}): AcademyReceipt => ({
  request_id: 'observe-request', status: 'ready', result: {world: session({status: 'observing', prediction: {option_id: 'option-0', correct: true}})}, finished_at: 3, ...patch,
})
const card = (overrides: Partial<Parameters<typeof AcademyWorldCard>[0]> = {}) => createElement(AcademyWorldCard, {
  kind: 'fraction-bar', session: null, params: {parts: 4, shaded: 1}, note: '', busy: false,
  onParams: () => {}, onNote: () => {}, onStart: () => {}, onPredict: () => {}, onObserve: () => {}, onComplete: () => {}, ...overrides,
})

it('matches world receipts by the write request and project', () => {
  expect(academyReceiptMatches(receipt(), 'observe-request', 'project')).toBe(true)
  expect(academyReceiptMatches(receipt(), 'observe-request', 'other')).toBe(false)
  expect(academyReceiptMatches(receipt(), 'start-request', 'project')).toBe(false)
  expect(academyReceiptMatches(receipt({status: 'unknown', result: {}}), 'observe-request', 'project')).toBe(true)
  expect(academyReceiptMatches(receipt({status: 'failed', result: {error_code: 'world-invalid'}}), 'observe-request', 'project')).toBe(true)
  expect(academyReceiptMatches(receipt({result: {}}), 'observe-request', 'project')).toBe(false)
  expect(Object.keys(academyWorldParams).sort()).toEqual(['balance-scale', 'float-sink', 'fraction-bar', 'group-array'])
})

it('visualizes kernel state for all three worlds', async () => {
  await setLocale('en')
  const fraction = renderToStaticMarkup(createElement(AcademyWorldState, {kind: 'fraction-bar', state: {parts: 6, shaded: 2, equal: true, label: '2/6', value: 2 / 6}}))
  expect(fraction).toContain('data-world-state="fraction-bar"')
  expect(fraction.match(/h-8 w-8/g)).toHaveLength(6)
  expect(fraction.match(/bg-brand/g)).toHaveLength(2)
  expect(fraction).toContain('2/6')
  const groups = renderToStaticMarkup(createElement(AcademyWorldState, {kind: 'group-array', state: {groups: 4, per_group: 3, total: 12, repeated: '3 + 3 + 3 + 3', product: '4 × 3'}}))
  expect(groups.match(/rounded-full bg-brand/g)).toHaveLength(12)
  expect(groups).toContain('3 + 3 + 3 + 3 = 12')
  const balance = renderToStaticMarkup(createElement(AcademyWorldState, {kind: 'balance-scale', state: {left: 7, right: 7, balanced: true, difference: 0}}))
  expect(balance).toContain('data-world-state="balance-scale"')
  expect(balance).toContain('7 g')
})

it('gates prediction, manipulation and completion by phase', async () => {
  await setLocale('en')
  const offer = renderToStaticMarkup(card())
  expect(offer).toContain('data-academy-world="offer"')
  expect(offer).toContain('Open the world')
  const predicting = renderToStaticMarkup(card({session: session()}))
  expect(predicting).toContain('data-academy-world="predicting"')
  expect(predicting).toContain('2/6')
  expect(predicting).not.toContain('answer_id')
  expect(predicting).toContain('disabled=""')
  const observing = renderToStaticMarkup(card({session: session({status: 'observing', prediction: {option_id: 'option-1', correct: false},
    observations: [{params: {parts: 6, shaded: 2}, state: {parts: 6, shaded: 2, equal: true, label: '2/6', value: 2 / 6}, at: 2}]})}))
  expect(observing).toContain('Your prediction differed')
  expect(observing).toContain('data-world-state="fraction-bar"')
  expect(observing).toContain('Observations: 1')
  expect(observing).toContain('disabled=""')
  const completed = renderToStaticMarkup(card({session: session({status: 'completed', prediction: {option_id: 'option-0', correct: true},
    note: 'The bottom counts equal parts.', completed_at: 4,
    observations: [{params: {parts: 6, shaded: 2}, state: {parts: 6, shaded: 2, equal: true, label: '2/6', value: 2 / 6}, at: 2}]})}))
  expect(completed).toContain('World activity completed.')
  expect(completed).toContain('The bottom counts equal parts.')
  expect(completed).not.toContain('Complete world activity')
})

it('renders nothing without a world task and switches language cleanly', async () => {
  await setLocale('en')
  const plain = {...lesson, world: undefined}
  expect(renderToStaticMarkup(createElement(AcademyWorld, {projectId: 'project', lesson: plain, active: false, disabled: false}))).toBe('')
  const english = renderToStaticMarkup(card({session: session()}))
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(card({session: session()}))
  expect(english).toContain('Lock prediction')
  expect(chinese).toContain('锁定预测')
  for (const html of [english, chinese]) expect(html).toContain('Set 6 equal parts and shade 2. What fraction?')
})
