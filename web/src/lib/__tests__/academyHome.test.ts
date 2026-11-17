import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it} from 'vitest'
import {setLocale} from '@/i18n'
import {AcademyHomeCard} from '@/components/inspector/AcademyHome'
import type {AcademyCourseSummary, AcademyProject} from '@/types'

afterEach(async () => {await setLocale('zh-CN')})

const summary: AcademyCourseSummary = {id: 'math', version: '1', origin: 'provided', title: 'Math', description: 'desc', lesson_count: 6, band: 'primary'}
const saved: AcademyProject = {id: 'p1', course_id: 'math', course_version: '1', name: 'Lab', revision: 1, active_lesson_id: 'l1',
  course_fingerprint: 'a'.repeat(64), created_at: 1, progress: [], root: '/x', fingerprint: 'a'.repeat(64), sandbox: 'unavailable'}
const card = (patch: Partial<Parameters<typeof AcademyHomeCard>[0]> = {}) => createElement(AcademyHomeCard, {
  item: summary, dueCount: 0, disabled: false, onStart: () => {}, onResume: () => {}, ...patch,
})

it('shows the cross-course due-review badge only when reviews are due', async () => {
  await setLocale('en')
  const due = renderToStaticMarkup(card({saved, dueCount: 3}))
  expect(due).toContain('data-home-due="3"')
  expect(due).toContain('3 due reviews')
  const none = renderToStaticMarkup(card({saved, dueCount: 0}))
  expect(none).not.toContain('data-home-due')
  const fresh = renderToStaticMarkup(card({dueCount: 3}))
  expect(fresh).not.toContain('data-home-due')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(card({saved, dueCount: 2}))
  expect(chinese).toContain('2 项待重测')
  expect(chinese).toContain('小学')
})
