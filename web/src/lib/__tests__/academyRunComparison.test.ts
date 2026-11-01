import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it} from 'vitest'
import {setLocale, t} from '@/i18n'
import {AcademyRunComparison} from '@/components/inspector/AcademyRunComparison'
import {academyCapturedOutputMatches, academyComparisonRuns} from '../academyRunComparison'
import type {AcademyRun} from '@/types'

const run = (id: string, changes: Partial<AcademyRun> = {}): AcademyRun => ({id, project_id: 'project', lesson_id: 'lesson', request_id: id,
  input_fingerprint: 'a'.repeat(64), input_changed: false, kind: 'run', created_at: 1, events: [],
  result: {status: 'passed', exit_code: 0, stdout: 'hello\n', stderr: '', truncated: false, duration_ms: 1, sandbox: 'fixture', memory_limited: false, peak_memory_kib: 0}, ...changes})
afterEach(async () => {await setLocale('zh-CN')})

it('uses only earlier records in persistent order from this project, lesson and kind', () => {
  const current = run('current')
  const before = run('before', {created_at: 100})
  const older = run('older', {created_at: 200})
  const rows = [run('newer'), current, run('foreign', {project_id: 'other'}), run('lesson', {lesson_id: 'other'}), run('check', {kind: 'check'}), before, before, older]
  expect(academyComparisonRuns(rows, current)).toEqual([before, older])
  expect(academyComparisonRuns(rows, run('missing'))).toEqual([])
  expect(academyComparisonRuns(rows, {...current, project_id: 'other'})).toEqual([])
})

it('bounds candidate choices and does not compare distinct stdout/stderr as one string', () => {
  const current = run('current')
  expect(academyComparisonRuns([current, ...Array.from({length: 20}, (_, index) => run(String(index)))], current)).toHaveLength(10)
  const first = {...current, result: {...current.result, stdout: 'a', stderr: 'b'}}
  const second = {...current, result: {...current.result, stdout: 'ab', stderr: ''}}
  expect(academyCapturedOutputMatches(first, second)).toBe(false)
  expect(academyCapturedOutputMatches(first, {...first, result: {...first.result, exit_code: 1, status: 'failed'}})).toBe(true)
  expect(academyCapturedOutputMatches(first, {...first, result: {...first.result, stderr: 'b\r\n'}})).toBe(false)
})

it('renders actual paired evidence, exact metadata, output changes and truncation in en/zh/en', async () => {
  const previous = run('previous', {input_fingerprint: 'b'.repeat(64), result: {...run('p').result, stdout: '以前\n', stderr: '<raw diagnostic>', truncated: true, exit_code: null}})
  const current = run('current', {input_changed: true, result: {...run('c').result, stdout: 'after\n', status: 'failed', exit_code: 1}})
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    const html = renderToStaticMarkup(createElement(AcademyRunComparison, {run: current, runs: [current, previous]}))
    for (const key of ['title', 'before', 'after', 'changedOutput', 'changedCode', 'truncated', 'exitUnknown']) expect(html).toContain(t('ui.academy.compare.' + key))
    expect(html).toContain('以前\n')
    expect(html).toContain('after\n')
    expect(html).toContain('&lt;raw diagnostic&gt;')
    expect(html).toContain(previous.input_fingerprint)
    expect(html).toContain(current.input_fingerprint)
    expect(html).toContain(t('ui.academy.inputChanged'))
    expect(html).not.toContain('<details open')
    const same = renderToStaticMarkup(createElement(AcademyRunComparison, {run: run('new'), runs: [run('new'), run('old')]}))
    expect(same).toContain(t('ui.academy.compare.sameOutput'))
    expect(same).toContain(t('ui.academy.compare.sameCode'))
  }
})

it('does not offer a comparison when no valid earlier peer exists', () => {
  const current = run('current')
  expect(renderToStaticMarkup(createElement(AcademyRunComparison, {run: current, runs: [current, run('check', {kind: 'check'})]}))).toBe('')
})
