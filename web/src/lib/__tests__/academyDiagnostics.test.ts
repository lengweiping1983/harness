import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it} from 'vitest'
import {setLocale, t} from '@/i18n'
import {AcademyRunOutput} from '@/components/inspector/AcademyRunOutput'
import {academyDiagnostic, academyLineRange} from '../academyDiagnostics'
import type {AcademyRun} from '@/types'

const trace = (file: string, line = '3', error = 'RuntimeError: learner failure') =>
  'Traceback (most recent call last):\n  File "/host/sandbox_entry.py", line 20, in <module>\n    run()\n  File "' + file + '", line ' + line + ', in <module>\n    raise RuntimeError("learner failure")\n' + error + '\n'
afterEach(async () => {await setLocale('zh-CN')})

it('selects the last manifest-owned frame and preserves the actual exception text', () => {
  expect(academyDiagnostic(trace('/project/辅助.py'), '/project/', ['main.py', '辅助.py'])).toEqual({summary: 'RuntimeError: learner failure', location: {path: '辅助.py', line: 3}})
  expect(academyDiagnostic(trace('/project/main.py') + 'retry\n详细备注\n', '/project', ['main.py'])?.summary).toBe('RuntimeError: learner failure')
  expect(academyDiagnostic(trace('/project/main.py') + '\nDuring handling of the above exception, another exception occurred:\n\n' + trace('/project/辅助.py', '7', 'ValueError: final error'), '/project', ['main.py', '辅助.py'])?.location).toEqual({path: '辅助.py', line: 7})
})

it('supports Windows frame spelling without offering paths outside the project manifest', () => {
  expect(academyDiagnostic(trace('c:\\Work\\Course\\HELPER.py'), 'C:\\work\\course', ['helper.py'])?.location).toEqual({path: 'helper.py', line: 3})
  for (const file of ['/project-other/main.py', '/project/../secret.py', '/project/not-listed.py', '<string>', '/host/sandbox_entry.py']) expect(academyDiagnostic(trace(file), '/project', ['main.py'])?.location).toBeUndefined()
})

it('supports syntax diagnostics and keeps unsupported diagnostics unprojected', () => {
  expect(academyDiagnostic('  File "/project/main.py", line 2\n    x =\n       ^\nSyntaxError: invalid syntax\n', '/project', ['main.py'])).toEqual({summary: 'SyntaxError: invalid syntax', location: {path: 'main.py', line: 2}})
  for (const stderr of ['', 'raw diagnostic', 'ValueError: no frame']) expect(academyDiagnostic(stderr, '/project', ['main.py'])).toBeNull()
  expect(academyDiagnostic(trace('/project/main.py', '0'), '/project', ['main.py'])?.location).toBeUndefined()
  expect(academyDiagnostic(trace('/project/main.py', '99999999999999999999'), '/project', ['main.py'])?.location).toBeUndefined()
})

it('computes exact UTF-16 source selections across mixed line endings and empty final lines', () => {
  const content = '🧪 = 1\r\nsecond\rthird\nfourth\n'
  for (const [line, expected] of [[1, '🧪 = 1'], [2, 'second'], [3, 'third'], [4, 'fourth'], [5, '']] as const) {
    const range = academyLineRange(content, line)!
    expect(content.slice(...range)).toBe(expected)
  }
  expect(academyLineRange('', 1)).toEqual([0, 0])
  for (const line of [0, -1, 1.5, Infinity, 6]) expect(academyLineRange(content, line)).toBeNull()
})

it('renders concise errors and preserves full output across en/zh/en', async () => {
  const run: AcademyRun = {id: 'run', project_id: 'project', lesson_id: 'lesson', request_id: 'request', input_fingerprint: 'a'.repeat(64), input_changed: false, kind: 'run', created_at: 1, events: [],
    result: {status: 'failed', exit_code: 1, stdout: 'before the failure\n', stderr: trace('/project/main.py'), truncated: false, duration_ms: 1, sandbox: 'fixture', memory_limited: false, peak_memory_kib: 0}}
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    const html = renderToStaticMarkup(createElement(AcademyRunOutput, {run, root: '/project', files: ['main.py'], canLocate: false, onLocate() {}}))
    expect(html).toContain('RuntimeError: learner failure')
    expect(html).toContain(t('ui.academy.fullRunOutput'))
    expect(html).toContain(t('ui.academy.errorLocation', {path: 'main.py', line: 3}))
    expect(html).toContain('disabled=""')
    expect(html).toContain('before the failure\nTraceback (most recent call last):')
    expect(html).not.toContain('<details open')
    const fallback = renderToStaticMarkup(createElement(AcademyRunOutput, {run: {...run, result: {...run.result, stderr: '<script>raw external diagnostic</script>'}}, root: '/project', files: ['main.py'], canLocate: true, onLocate() {}}))
    expect(fallback).not.toContain('<details')
    expect(fallback).toContain('&lt;script&gt;raw external diagnostic&lt;/script&gt;')
  }
})
