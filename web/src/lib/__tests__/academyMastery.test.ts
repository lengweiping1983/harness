import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it} from 'vitest'
import {setLocale} from '@/i18n'
import {AcademyEngagementStats, AcademyMastery, AcademyReport, AcademyReviewCard, AcademyReviewSession, AcademySkillList} from '@/components/inspector/AcademyMastery'
import {AcademyMistakeJournal} from '@/components/inspector/AcademyMistakeJournal'
import {AcademyMeasurement} from '@/components/inspector/AcademyMeasurement'
import {academyEfficacyMetrics, academyMasteryCounts, academyReceiptMatches, academyReviewAnswers, academyReviewComplete, academyReviewOrder} from '../academy'
import type {AcademyMistake, AcademyReceipt, AcademyReview, AcademyReviewItem, AcademySkillState} from '@/types'

afterEach(async () => {await setLocale('zh-CN')})

const skill = (patch: Partial<AcademySkillState> = {}): AcademySkillState => ({
  lesson_id: 'fractions', title: 'Fractions', stage: 'Stage A', state: 'learning',
  stability_days: 2.5, difficulty: 5, retrievability: 0.81, due_at: 100, last_evidence_at: 1,
  evidence_count: 1, reviews: {attempts: 1, correct: 1, delayed_correct: 0, delayed_attempts: 0}, ...patch,
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
  // 置信度可选：合法值随答案提交，非法值不携带。
  expect(academyReviewAnswers(item, {q2: 'b', q1: 'right'}, {q1: 'sure', q2: 'guess'})).toEqual([{check_id: 'q1', option_id: 'right', confidence: 'sure'}, {check_id: 'q2', option_id: 'b', confidence: 'guess'}])
  expect(academyReviewAnswers(item, {q2: 'b', q1: 'right'}, {q1: 'bogus'})).toEqual([{check_id: 'q1', option_id: 'right'}, {check_id: 'q2', option_id: 'b'}])
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

it('shows the next review date on skills with evidence', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(createElement(AcademySkillList, {skills: [skill({due_at: 1794943740})]}))
  expect(html).toContain('Next review:')
  const none = renderToStaticMarkup(createElement(AcademySkillList, {skills: [skill({due_at: null})]}))
  expect(none).not.toContain('Next review:')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(createElement(AcademySkillList, {skills: [skill({due_at: 1794943740})]}))
  expect(chinese).toContain('下次重测：')
})
it('renders the learning report tiles from mastery evidence bilingually', async () => {
  await setLocale('en')
  const mastery = {skills: [], due_count: 0, reviews: {attempts: 4, correct: 3, delayed_correct: 2, delayed_attempts: 2},
    engagement: {constructive: 1, active: 1, total: 2, constructive_ratio: 0.5}, repairs: 2, transfers: 3, blindspots_open: 1, streak_days: 3,
    calibration: {sure_correct: 4, sure_wrong: 1, unsure_correct: 2, unsure_wrong: 1, guess_correct: 3, guess_wrong: 0},
    activity: Array.from({length: 14}, (_, i) => ({date: '2026-11-' + String(i + 1).padStart(2, '0'), evidence: i % 3}))}
  const html = renderToStaticMarkup(createElement(AcademyReport, {mastery}))
  expect(html).toContain('data-academy-report')
  expect(html).toContain('Blindspots repaired')
  expect(html).toContain('Transfer successes')
  expect(html).toContain('50%')
  expect(html).toContain('Day streak (evidence)')
  expect(html).toContain('Export evidence (JSON)')
  expect(html).toContain('Sure &amp; wrong (blindspots)')
  expect(html).toContain('Guess &amp; correct')
  expect(html).toContain('data-academy-badges')
  expect(html).toContain('Blindspot repairer')
  expect(html).toContain('Transfer achiever')
  expect(html).toContain('3-day evidence streak')
  expect(html).not.toContain('3 durable skills')
  expect(html).toContain('data-academy-activity')
  expect(html).toContain('Evidence per day (last 14 days)')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(createElement(AcademyReport, {mastery}))
  expect(chinese).toContain('已修复盲点')
  expect(chinese).toContain('延迟重测正确率')
})
it('renders the shared review session shell without fetching when inactive', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(createElement(AcademyReviewSession, {projectId: 'project', active: false}))
  expect(html).toContain('data-academy-review-session')
  expect(html).not.toContain('role="status"')
})

it('renders review questions without answers, feedback or hints and disables incomplete submission', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(createElement(AcademyReviewCard, {item: reviewItem(), busy: false, onSubmit: () => {}}))
  expect(html).toContain('data-academy-review-lesson="fractions"')
  expect(html).toContain('Which is larger?')
  expect(html).toContain('One half')
  expect(html).toContain('Overdue 1.2 days')
  expect(html).toContain('data-review-rationale')
  expect(html).toContain('Retention 81%')
  expect(html).toContain('spacing effect')
  expect(html).toContain('width:81%')
  expect(html).toContain('disabled')
  expect(html).not.toContain('answer_id')
  expect(html).not.toContain('feedback')
  expect(html).not.toContain('hint')
})

it('presents one review question at a time with progress dots and gated submission', async () => {
  await setLocale('en')
  const item = reviewItem({checks: [
    {id: 'q1', prompt: 'Which is larger?', options: [{id: 'right', label: 'One half'}, {id: 'wrong', label: 'One third'}]},
    {id: 'q2', prompt: 'Which is smaller?', options: [{id: 'a', label: 'One third'}, {id: 'b', label: 'One half'}]},
  ]})
  const html = renderToStaticMarkup(createElement(AcademyReviewCard, {item, busy: false, onSubmit: () => {}}))
  expect(html).toContain('data-review-card')
  expect(html).toContain('Which is larger?')
  expect(html).not.toContain('Which is smaller?')
  expect(html).toContain('data-review-dot="0"')
  expect(html).toContain('data-review-dot="1"')
  expect(html).toContain('Question 1 of 2')
  expect(html).toContain('data-review-prev')
  expect(html).toContain('data-review-next')
  expect(html).not.toContain('data-review-continue')
  expect(html).toContain('data-confidence="sure"')
  expect(html).toContain('data-confidence="guess"')
  expect(html).toContain('Before you answer: how sure are you?')
  expect(html).toContain('disabled')
  expect(html).not.toContain('answer_id')
  expect(html).not.toContain('feedback')
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

it('renders the mistake journal with badges, open filter and retry for open mistakes only', async () => {
  await setLocale('en')
  const mistakes: AcademyMistake[] = [
    {lesson_id: 'fractions', lesson_title: 'Fractions', step_id: 's1', prompt: 'How many?', response: '9', confidence: 'sure', diagnosis: 'Unequal parts', blindspot: true, repaired: false, at: 100},
    {lesson_id: 'decimals', lesson_title: 'Decimals', step_id: 's2', prompt: 'Which?', response: '0.3', blindspot: true, repaired: true, at: 90},
  ]
  const html = renderToStaticMarkup(createElement(AcademyMistakeJournal, {mistakes, onOpenLesson: () => {}}))
  expect(html).toContain('data-academy-journal')
  expect(html).toContain('Mistake journal')
  expect(html).toContain('data-journal-badge="blindspot"')
  expect(html).toContain('data-journal-badge="repaired"')
  expect(html).toContain('Your answer: 9')
  expect(html).toContain('Your diagnosis: Unequal parts')
  expect(html).toContain('data-journal-retry="fractions"')
  expect(html).not.toContain('data-journal-retry="decimals"')
  expect(html).toContain('data-journal-filter="open"')
  const empty = renderToStaticMarkup(createElement(AcademyMistakeJournal, {}))
  expect(empty).toContain('data-academy-journal-empty')
  expect(empty).toContain('No mistakes yet')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(createElement(AcademyMistakeJournal, {mistakes}))
  expect(chinese).toContain('错题本')
  expect(chinese).toContain('盲点')
  expect(chinese).toContain('已修复')
  expect(chinese).toContain('你的作答：9')
})

it('shows delayed-review evidence per skill only when it exists', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(createElement(AcademySkillList, {skills: [skill({reviews: {attempts: 3, correct: 3, delayed_correct: 2, delayed_attempts: 2}}), skill({lesson_id: 'decimals', title: 'Decimals', reviews: {attempts: 1, correct: 1, delayed_correct: 0, delayed_attempts: 0}})]}))
  expect(html).toContain('data-skill-delayed="2"')
  expect(html).toContain('Delayed review correct 2x')
  expect(html).not.toContain('data-skill-delayed="0"')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(createElement(AcademySkillList, {skills: [skill({reviews: {attempts: 3, correct: 3, delayed_correct: 2, delayed_attempts: 2}})]}))
  expect(chinese).toContain('延迟重测答对 2 次')
})

it('compares immediate and delayed retention with honest empty states', async () => {
  await setLocale('en')
  const mastery = {skills: [], due_count: 0, reviews: {attempts: 5, correct: 4, delayed_correct: 2, delayed_attempts: 3},
    engagement: {constructive: 1, active: 1, total: 2, constructive_ratio: 0.5}}
  const html = renderToStaticMarkup(createElement(AcademyReport, {mastery}))
  expect(html).toContain('data-academy-retention')
  expect(html).toContain('Retention comparison (testing effect)')
  expect(html).toContain('data-retention-immediate')
  expect(html).toContain('(2/2)')
  expect(html).toContain('data-retention-delayed')
  expect(html).toContain('67%')
  expect(html).toContain('(2/3)')
  const empty = renderToStaticMarkup(createElement(AcademyReport, {mastery: {...mastery, reviews: {attempts: 0, correct: 0, delayed_correct: 0, delayed_attempts: 0}}}))
  expect(empty).toContain('No review evidence yet')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(createElement(AcademyReport, {mastery}))
  expect(chinese).toContain('保持率对比')
  expect(chinese).toContain('延迟正确率')
})

it('shows transfer pass rate only when transfer steps were attempted', async () => {
  await setLocale('en')
  const base = {skills: [], due_count: 0, reviews: {attempts: 0, correct: 0, delayed_correct: 0, delayed_attempts: 0},
    engagement: {constructive: 0, active: 0, total: 0, constructive_ratio: 0}}
  const attempted = renderToStaticMarkup(createElement(AcademyReport, {mastery: {...base, transfers: 1, transfer_attempts: 2}}))
  expect(attempted).toContain('Transfer pass rate')
  expect(attempted).toContain('50%')
  const none = renderToStaticMarkup(createElement(AcademyReport, {mastery: base}))
  expect(none).not.toContain('Transfer pass rate')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(createElement(AcademyReport, {mastery: {...base, transfers: 2, transfer_attempts: 2}}))
  expect(chinese).toContain('迁移通过率')
  expect(chinese).toContain('100%')
})

it('shows focused learning time per skill when recorded', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(createElement(AcademySkillList, {skills: [skill({learning_seconds: 120}), skill({lesson_id: 'decimals', title: 'Decimals', learning_seconds: 0})]}))
  expect(html).toContain('data-skill-focus="120"')
  expect(html).toContain('2 min focused')
  expect(html).not.toContain('data-skill-focus="0"')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(createElement(AcademySkillList, {skills: [skill({learning_seconds: 90})]}))
  expect(chinese).toContain('专注 2 分钟')
})

it('orders review queue interleaved by default and weakest-first on demand', () => {
  const queue = [{retrievability: 0.8, id: 'a'}, {retrievability: 0.3, id: 'b'}, {retrievability: 0.6, id: 'c'}]
  expect(academyReviewOrder(queue, false).map(item => item.id)).toEqual(['a', 'b', 'c'])
  expect(academyReviewOrder(queue, true).map(item => item.id)).toEqual(['b', 'c', 'a'])
  expect(queue.map(item => item.id)).toEqual(['a', 'b', 'c'])
})

it('aggregates efficacy metrics with honest null states and minutes per durable skill', () => {
  const mastery = {skills: [skill({state: 'durable', learning_seconds: 180}), skill({lesson_id: 'decimals', state: 'learning', learning_seconds: 60}), skill({lesson_id: 'ratios', state: 'new'})],
    due_count: 0, reviews: {attempts: 5, correct: 4, delayed_correct: 2, delayed_attempts: 3},
    engagement: {constructive: 1, active: 1, total: 2, constructive_ratio: 0.5}, transfers: 1, transfer_attempts: 2, repairs: 3, blindspots_open: 1, streak_days: 4}
  const metrics = academyEfficacyMetrics(mastery)
  expect(metrics.delayedRate).toBe(67)
  expect(metrics.transferRate).toBe(50)
  expect(metrics.repairRate).toBe(75)
  expect(metrics.durable).toBe(1)
  expect(metrics.minutesPerDurable).toBe(3)
  expect(metrics.totalFocusMinutes).toBe(4)
  expect(metrics.hasDelayedEvidence).toBe(true)
  const empty = academyEfficacyMetrics({skills: [], due_count: 0, reviews: {attempts: 0, correct: 0, delayed_correct: 0, delayed_attempts: 0}, engagement: {constructive: 0, active: 0, total: 0, constructive_ratio: 0}})
  expect(empty.delayedRate).toBeNull()
  expect(empty.transferRate).toBeNull()
  expect(empty.repairRate).toBeNull()
  expect(empty.minutesPerDurable).toBeNull()
  expect(empty.hasDelayedEvidence).toBe(false)
})

it('renders the measurement dashboard with metrics, protocol and honest empty states', async () => {
  await setLocale('en')
  const mastery = {skills: [skill({state: 'durable', learning_seconds: 180})], due_count: 0, reviews: {attempts: 3, correct: 2, delayed_correct: 2, delayed_attempts: 3},
    engagement: {constructive: 1, active: 1, total: 2, constructive_ratio: 0.5}, transfers: 1, transfer_attempts: 2, repairs: 1, blindspots_open: 1}
  const html = renderToStaticMarkup(createElement(AcademyMeasurement, {mastery}))
  expect(html).toContain('data-academy-measurement')
  expect(html).toContain('Learning efficacy measurement')
  expect(html).toContain('observational evidence')
  expect(html).toContain('data-measurement-retention')
  expect(html).toContain('67%')
  expect(html).toContain('data-measurement-transfer')
  expect(html).toContain('50%')
  expect(html).toContain('Rigorous measurement protocol')
  expect(html).toContain('data-measurement-export')
  expect(html).not.toContain('No delayed-review evidence yet')
  const empty = renderToStaticMarkup(createElement(AcademyMeasurement, {mastery: {skills: [], due_count: 0, reviews: {attempts: 0, correct: 0, delayed_correct: 0, delayed_attempts: 0}, engagement: {constructive: 0, active: 0, total: 0, constructive_ratio: 0}}}))
  expect(empty).toContain('No delayed-review evidence yet')
  expect(empty).toContain('No durable skills yet')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(createElement(AcademyMeasurement, {mastery}))
  expect(chinese).toContain('学习效果实测')
  expect(chinese).toContain('延迟保持率')
  expect(chinese).toContain('严谨实测协议')
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
