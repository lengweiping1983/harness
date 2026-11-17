import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it} from 'vitest'
import {setLocale} from '@/i18n'
import {AcademyEngagementStats, AcademyMastery, AcademyReviewCard, AcademySkillList} from '@/components/inspector/AcademyMastery'
import {academyMasteryCounts, academyReceiptMatches, academyReviewAnswers, academyReviewComplete} from '../academy'
import type {AcademyReceipt, AcademyReview, AcademyReviewItem, AcademySkillState} from '@/types'

afterEach(async () => {await setLocale('zh-CN')})

const skill = (patch: Partial<AcademySkillState> = {}): AcademySkillState => ({
  lesson_id: 'fractions', title: 'Fractions', stage: 'Stage A', state: 'learning',
  stability_days: 2.5, difficulty: 5, retrievability: 0.81, due_at: 100, last_evidence_at: 1,
  evidence_count: 1, reviews: {attempts: 1, correct: 1, delayed_correct: 0}, ...patch,
})
const reviewItem = (patch: Partial<AcademyReviewItem> = {}): AcademyReviewItem => ({
  lesson_id: 'fractions', title: 'Fractions', goal: 'Compare fractions', stage: 'Stage A',
  retrievability: 0.81, overdue_days: 1.2,
  checks: [{id: 'q1', prompt: 'Which is larger?', options: [{id: 'right', label: 'One half'}, {id: 'wrong', label: 'One third'}]}], ...patch,
})
const review = (patch: Partial<AcademyReview> = {}): AcademyReview => ({
  id: 'review-one', project_id: 'project', lesson_id: 'fractions', request_id: 'request-one',
  answers: [{check_id: 'q1', option_id: 'right'}], results: [{check_id: 'q1', correct: true}],
  correct: true, elapsed_days: 3, retrievability: 0.81, created_at: 10, ...patch,
})
const receipt = (patch: Partial<AcademyReceipt> = {}): AcademyReceipt => ({
  request_id: 'request-one', status: 'ready', result: {review: review(), skill: skill()}, finished_at: 10, ...patch,
})

it('accepts only review receipts bound to the original request and project', () => {
  expect(academyReceiptMatches(receipt(), 'request-one', 'project')).toBe(true)
  expect(academyReceiptMatches(receipt(), 'request-one', 'other-project')).toBe(false)
  expect(academyReceiptMatches(receipt(), 'request-two', 'project')).toBe(false)
  expect(academyReceiptMatches(receipt({result: {review: review({request_id: 'other'}), skill: skill()}}), 'request-one', 'project')).toBe(false)
  expect(academyReceiptMatches(receipt({status: 'unknown', result: {}}), 'request-one', 'project')).toBe(true)
  expect(academyReceiptMatches(receipt({status: 'failed', result: {error_code: 'review-incomplete'}}), 'request-one', 'project')).toBe(true)
  expect(academyReceiptMatches(receipt({status: 'failed', result: {}}), 'request-one', 'project')).toBe(false)
  expect(academyReceiptMatches(receipt({result: {}}), 'request-one', 'project')).toBe(false)
  expect(academyReceiptMatches(receipt({result: {skill: skill()}}), 'request-one', 'project')).toBe(false)
})

it('requires every check answered with a real option before submission', () => {
  const item = reviewItem()
  expect(academyReviewComplete(item, {})).toBe(false)
  expect(academyReviewComplete(item, {q1: 'nope'})).toBe(false)
  expect(academyReviewComplete(item, {q1: 'right'})).toBe(true)
  expect(academyReviewComplete(reviewItem({checks: []}), {})).toBe(false)
  const two = reviewItem({checks: [...item.checks, {id: 'q2', prompt: 'Again?', options: [{id: 'a', label: 'A'}, {id: 'b', label: 'B'}]}]})
  expect(academyReviewComplete(two, {q1: 'right'})).toBe(false)
  expect(academyReviewComplete(two, {q1: 'right', q2: 'b'})).toBe(true)
})

it('builds answers by check identity in course order', () => {
  const item = reviewItem({checks: [...reviewItem().checks, {id: 'q2', prompt: 'Again?', options: [{id: 'a', label: 'A'}, {id: 'b', label: 'B'}]}]})
  expect(academyReviewAnswers(item, {q2: 'b', q1: 'right'})).toEqual([{check_id: 'q1', option_id: 'right'}, {check_id: 'q2', option_id: 'b'}])
})

it('counts mastery states without projecting missing evidence as mastery', () => {
  expect(academyMasteryCounts({skills: [skill(), skill({lesson_id: 'decimals', state: 'durable'}), skill({lesson_id: 'ratios', state: 'new', evidence_count: 0})]}))
    .toEqual({durable: 1, learning: 1, fresh: 1})
})

it('renders skill states and keeps unstarted skills unlabelled as mastery', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(createElement(AcademySkillList, {skills: [skill(), skill({lesson_id: 'ratios', title: 'Ratios', state: 'new', evidence_count: 0, retrievability: 1, due_at: null, last_evidence_at: null})]}))
  expect(html).toContain('data-skill-id="fractions"')
  expect(html).toContain('Consolidating')
  expect(html).toContain('Not started')
  expect(html).toContain('Recall 81%')
  expect(html).toContain('Stability 2.5 days')
  const empty = renderToStaticMarkup(createElement(AcademySkillList, {skills: [skill({state: 'new', evidence_count: 0})]}))
  expect(empty).toContain('Complete a lesson activity or pass a code check')
})

it('renders review questions without answers, feedback or hints and disables incomplete submission', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(createElement(AcademyReviewCard, {item: reviewItem(), busy: false, onSubmit: () => {}}))
  expect(html).toContain('data-academy-review-lesson="fractions"')
  expect(html).toContain('Which is larger?')
  expect(html).toContain('One half')
  expect(html).toContain('Overdue 1.2 days')
  expect(html).toContain('disabled')
  expect(html).not.toContain('answer_id')
  expect(html).not.toContain('feedback')
  expect(html).not.toContain('hint')
})

it('switches interface language without rewriting option identities', async () => {
  await setLocale('en')
  const english = renderToStaticMarkup(createElement(AcademyReviewCard, {item: reviewItem(), busy: false, onSubmit: () => {}}))
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(createElement(AcademyReviewCard, {item: reviewItem(), busy: false, onSubmit: () => {}}))
  expect(english).toContain('Submit review')
  expect(chinese).toContain('提交重测')
  for (const html of [english, chinese]) {
    expect(html).toContain('value="right"')
    expect(html).toContain('value="wrong"')
    expect(html).toContain('One half')
  }
})

it('renders the mastery entry collapsed without fetching when inactive', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(createElement(AcademyMastery, {projectId: 'project', active: false}))
  expect(html).toContain('data-academy-section="mastery"')
  expect(html).toContain('Memory &amp; delayed review')
  expect(html).not.toContain('role="status"')
})

it('rates engagement without projecting zero evidence as participation', async () => {
  await setLocale('en')
  expect(renderToStaticMarkup(createElement(AcademyEngagementStats, {engagement: {constructive: 0, active: 0, total: 0, constructive_ratio: 0}}))).toBe('')
  const html = renderToStaticMarkup(createElement(AcademyEngagementStats, {engagement: {constructive: 1, active: 2, total: 3, constructive_ratio: 0.3333}}))
  expect(html).toContain('data-academy-engagement')
  expect(html).toContain('1 constructive')
  expect(html).toContain('2 active')
  expect(html).toContain('33% constructive')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(createElement(AcademyEngagementStats, {engagement: {constructive: 1, active: 2, total: 3, constructive_ratio: 0.3333}}))
  expect(chinese).toContain('建构占比 33%')
})
