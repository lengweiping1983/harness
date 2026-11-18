import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it, vi} from 'vitest'
import {setLocale} from '@/i18n'
import {AcademyDailyGoal} from '@/components/inspector/AcademyDailyGoal'
import {AcademyReport} from '@/components/inspector/AcademyMastery'
import {academyDailyGoalRead, academyDailyGoalWrite, academyTodayEvidence, academyTodayKey} from '../academyGoal'
import type {AcademyActivityDay, AcademyMastery} from '@/types'

afterEach(async () => {await setLocale('zh-CN'); vi.unstubAllGlobals()})

const stubStorage = (initial: Record<string, string> = {}) => {
  const store = new Map(Object.entries(initial))
  vi.stubGlobal('localStorage', {getItem: (key: string) => store.get(key) ?? null, setItem: (key: string, value: string) => {store.set(key, value)}, removeItem: (key: string) => {store.delete(key)}})
  return store
}
const activity = (todayEvidence: number): AcademyActivityDay[] => Array.from({length: 14}, (_, i) => {
  const date = academyTodayKey(new Date(Date.now() - (13 - i) * 86400000))
  return {date, evidence: i === 13 ? todayEvidence : i % 3}
})

it('reads and writes the daily goal with safe fallbacks', () => {
  const store = stubStorage()
  expect(academyDailyGoalRead()).toBeNull()
  academyDailyGoalWrite(5)
  expect(academyDailyGoalRead()).toBe(5)
  expect(JSON.parse(store.get('harness.academy.goal.v1')!)).toBe(5)
  academyDailyGoalWrite(null)
  expect(academyDailyGoalRead()).toBeNull()
  store.set('harness.academy.goal.v1', '{"daily":3}')
  expect(academyDailyGoalRead()).toBeNull()
  store.set('harness.academy.goal.v1', '100')
  expect(academyDailyGoalRead()).toBeNull()
  store.set('harness.academy.goal.v1', 'broken')
  expect(academyDailyGoalRead()).toBeNull()
})

it('counts today evidence by local date without projecting missing days', () => {
  expect(academyTodayEvidence(undefined)).toBe(0)
  expect(academyTodayEvidence([])).toBe(0)
  expect(academyTodayEvidence(activity(4))).toBe(4)
  expect(academyTodayEvidence([{date: '2000-01-01', evidence: 9}])).toBe(0)
})

it('renders goal states bilingually: unset, active, attained', async () => {
  await setLocale('en')
  stubStorage()
  const unset = renderToStaticMarkup(createElement(AcademyDailyGoal, {activity: activity(1)}))
  expect(unset).toContain('data-daily-goal="unset"')
  expect(unset).toContain('Set a daily goal')
  expect(unset).toContain('not logins or clicks')
  stubStorage({'harness.academy.goal.v1': '3'})
  const active = renderToStaticMarkup(createElement(AcademyDailyGoal, {activity: activity(1), compact: true}))
  expect(active).toContain('data-daily-goal="active"')
  expect(active).toContain('Evidence today 1/3')
  expect(active).not.toContain('not logins or clicks')
  const attained = renderToStaticMarkup(createElement(AcademyDailyGoal, {activity: activity(3)}))
  expect(attained).toContain('data-daily-goal="attained"')
  expect(attained).toContain('Daily goal reached')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(createElement(AcademyDailyGoal, {activity: activity(5)}))
  expect(chinese).toContain('今日证据 5/3')
  expect(chinese).toContain('今日目标达成')
  stubStorage()
  const chineseUnset = renderToStaticMarkup(createElement(AcademyDailyGoal, {}))
  expect(chineseUnset).toContain('设定今日目标')
})

it('renders the activity heatmap with intensity levels and attained-today ring', async () => {
  await setLocale('en')
  stubStorage({'harness.academy.goal.v1': '3'})
  const mastery: AcademyMastery = {skills: [], due_count: 0, reviews: {attempts: 1, correct: 1, delayed_correct: 0},
    engagement: {constructive: 1, active: 1, total: 2, constructive_ratio: 0.5}, activity: activity(5)}
  const html = renderToStaticMarkup(createElement(AcademyReport, {mastery}))
  expect(html).toContain('data-academy-activity')
  const today = academyTodayKey()
  expect(html).toContain('data-activity-day="' + today + '"')
  const cell = html.slice(html.indexOf('data-activity-day="' + today + '"'))
  expect(cell.slice(0, 200)).toContain('ring-ok')
  expect(cell.slice(0, 200)).toContain('bg-brand')
  // 无目标时今日仅品牌描边。
  stubStorage()
  const plain = renderToStaticMarkup(createElement(AcademyReport, {mastery}))
  const plainCell = plain.slice(plain.indexOf('data-activity-day="' + today + '"'))
  expect(plainCell.slice(0, 200)).toContain('ring-brand')
  expect(plainCell.slice(0, 200)).not.toContain('ring-ok')
})
