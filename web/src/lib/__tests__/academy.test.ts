import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it, vi} from 'vitest'
import {setLocale, t} from '@/i18n'
import {AcademyAssessment, AcademyRubric} from '@/components/inspector/AcademyAssessment'
import {AcademyLearningPath} from '@/components/inspector/AcademyLearningPath'
import {noticeText} from '../noticeText'
import {academyApi, academyCourseProject, academyActiveLessonCompleted, academyDimensions, academyLessonRun, academyRadarPoint, academyReceiptMatches, academyResumeLesson, academyStages, isAcademyUnconfirmedWrite, watchAcademyModelRuns, watchAcademySubmissions} from '../academy'
import type {ApiResult} from '../http'
import type {AcademyCourse, AcademyLesson, AcademyModelRun, AcademyProject, AcademyReceipt, AcademyRun, AcademySubmission} from '@/types'

const submission = (patch: Partial<AcademySubmission> = {}): AcademySubmission => ({
  id: 'submission-one', project_id: 'my-project', lesson_id: 'day-01', request_id: 'request-one',
  input_fingerprint: 'a'.repeat(64), created_at: 1, note: 'My prediction', status: 'evaluating',
  model: 'original-model', language: 'en', ...patch,
})
const response = (submissions: AcademySubmission[]): ApiResult<{submissions: AcademySubmission[]}> => ({ok: true, data: {submissions}})
afterEach(async () => {vi.useRealTimers(); vi.unstubAllGlobals(); await setLocale('zh-CN')})

const lesson = (id: string, stage?: string): AcademyLesson => ({id, stage, mode: 'python', order: 1, title: id, goal: id,
  reading: id, milestone: false, run_command: ['python', 'main.py'], source_manifest: [], references: [], prerequisites: [], trace_labels: {}})
const learningCourse: AcademyCourse = {id: 'course', version: '1', origin: 'provided', source_revision: 'fixture', title: 'Course', description: '', references: [],
  lessons: [lesson('one', 'Start'), lesson('two', 'Start'), lesson('three', 'Apply'), lesson('four', 'Start')]}
const learningProject: AcademyProject = {id: 'project', course_id: 'course', course_version: '1', name: 'Project', revision: 1,
  active_lesson_id: 'two', course_fingerprint: 'a'.repeat(64), created_at: 1, progress: [], root: '/fixture', fingerprint: 'a'.repeat(64), sandbox: 'unavailable'}

it('continues the current course project before the latest created one and isolates course versions', () => {
  const older = {...learningProject, id: 'older', created_at: 1}
  const newer = {...learningProject, id: 'newer', created_at: 4}
  const wrongVersion = {...newer, id: 'other-version', course_version: '2', created_at: 8}
  const otherCourse = {...newer, id: 'other-course', course_id: 'other', created_at: 9}
  const projects = [otherCourse, wrongVersion, older, newer]
  expect(academyCourseProject(learningCourse, projects, older.id)).toBe(older)
  expect(academyCourseProject(learningCourse, projects, otherCourse.id)).toBe(newer)
  expect(academyCourseProject(learningCourse, [...projects].reverse(), '')).toBe(newer)
  expect(academyCourseProject(learningCourse, [otherCourse, wrongVersion], '')).toBeUndefined()
  expect(projects).toEqual([otherCourse, wrongVersion, older, newer])
})

it('keeps nonadjacent stages separate and uses first-lesson identities across translations', () => {
  expect(academyStages(learningCourse).map(stage => [stage.id, stage.lessons.map(lesson => lesson.id)]))
    .toEqual([['one', ['one', 'two']], ['three', ['three']], ['four', ['four']]])
  const translated = {...learningCourse, lessons: learningCourse.lessons.map(lesson => ({...lesson, stage: lesson.stage === 'Start' ? '入门' : '迁移'}))}
  expect(academyStages(translated).map(stage => stage.id)).toEqual(academyStages(learningCourse).map(stage => stage.id))
  expect(academyStages({lessons: [lesson('standalone')]})).toEqual([{id: 'standalone', title: undefined, lessons: [lesson('standalone')]}])
})

it('resumes the open unfinished lesson and recommends remaining lessons using code-check evidence', () => {
  expect(academyResumeLesson(learningCourse, learningProject)?.id).toBe('two')
  const passed = {...learningProject, progress: [{lesson_id: 'two', state: 'passed' as const, checkpoint_id: '', evidence_ids: ['evidence'], attempt_count: 1}]}
  expect(academyResumeLesson(learningCourse, passed)?.id).toBe('one')
  expect(academyResumeLesson(learningCourse, {...learningProject, course_id: 'other'})).toBeUndefined()
  expect(academyResumeLesson(learningCourse, {...passed, progress: learningCourse.lessons.map(lesson => ({...passed.progress[0], lesson_id: lesson.id}))})).toBeUndefined()
})

it('does not present the newest result from another lesson or project as this lesson run', () => {
  const run = (project_id: string, lesson_id: string): AcademyRun => ({id: project_id + lesson_id, project_id, lesson_id,
    request_id: 'request', input_fingerprint: 'a'.repeat(64), input_changed: false, kind: 'check', created_at: 1, events: [],
    result: {status: 'passed', exit_code: 0, stdout: 'pass', stderr: '', truncated: false, duration_ms: 1, sandbox: 'fixture', memory_limited: false, peak_memory_kib: 0}})
  const current = run('project', 'one')
  expect(academyLessonRun([run('other', 'one'), run('project', 'two'), current], 'project', 'one')).toBe(current)
  expect(academyLessonRun([current], 'project', 'two')).toBeUndefined()
})

it('offers continuation only for current code-check evidence owned by the active project and course version', () => {
  const course = {...learningCourse, lessons: [lesson('two'), lesson('three')]}
  const project: AcademyProject = {...learningProject, progress: [{lesson_id: 'two', state: 'passed', checkpoint_id: 'checkpoint', evidence_ids: ['check'], attempt_count: 1}]}
  const run: AcademyRun = {id: 'check', project_id: project.id, lesson_id: 'two', request_id: 'request',
    input_fingerprint: project.fingerprint, input_changed: false, kind: 'check', created_at: 1, events: [],
    result: {status: 'passed', exit_code: 0, stdout: '', stderr: '', truncated: false, duration_ms: 1, sandbox: 'fixture', memory_limited: false, peak_memory_kib: 0}}
  expect(academyActiveLessonCompleted(course, project, [run])).toBe(true)
  expect(academyActiveLessonCompleted({...course, id: 'other'}, project, [run])).toBe(false)
  expect(academyActiveLessonCompleted({...course, version: '2'}, project, [run])).toBe(false)
  expect(academyActiveLessonCompleted(course, {...project, active_lesson_id: 'three'}, [run])).toBe(false)
  expect(academyActiveLessonCompleted(course, {...project, fingerprint: 'b'.repeat(64)}, [run])).toBe(false)
  for (const mutation of [{id: 'unrelated'}, {project_id: 'other'}, {lesson_id: 'three'}, {kind: 'run' as const}, {input_changed: true},
    {result: {...run.result, status: 'failed' as const}}, {result: {...run.result, exit_code: null}}]) {
    expect(academyActiveLessonCompleted(course, project, [{...run, ...mutation}])).toBe(false)
  }
  expect(academyActiveLessonCompleted(course, project, [])).toBe(false)
})

it.each(['study', 'model'] as const)('requires an explicit completed %s activity rather than a generated response or code-check state', mode => {
  const course = {...learningCourse, lessons: [{...lesson('two'), mode}]}
  const progress = {lesson_id: 'two', state: 'completed' as const, checkpoint_id: 'checkpoint', evidence_ids: ['activity'], attempt_count: 1}
  const project = {...learningProject, progress: [progress]}
  expect(academyActiveLessonCompleted(course, project, [])).toBe(true)
  expect(academyActiveLessonCompleted(course, {...project, progress: [{...progress, state: 'in_progress'}]}, [])).toBe(false)
  expect(academyActiveLessonCompleted(course, {...project, progress: [{...progress, state: 'passed'}]}, [])).toBe(false)
  expect(academyActiveLessonCompleted(course, {...project, progress: [{...progress, evidence_ids: []}]}, [])).toBe(false)
})

it('rejects a receipt from another request before applying project state', () => {
  const receipt: AcademyReceipt = {request_id: 'old-request', status: 'failed', result: {error_code: 'stale-file'}}
  expect(academyReceiptMatches(receipt, 'new-request')).toBe(false)
  expect(academyReceiptMatches(receipt, 'old-request')).toBe(true)
})

it('unknown receipts retain the original identity without inventing a result', () => {
  const receipt: AcademyReceipt = {request_id: 'pending', status: 'unknown', result: {}}
  expect(academyReceiptMatches(receipt, 'pending', 'my-project')).toBe(true)
  expect(receipt.status).toBe('unknown')
})

it('accepts a submission receipt only for its exact request and project', () => {
  const receipt: AcademyReceipt = {request_id: 'request-one', status: 'ready', result: {submission: submission()}}
  expect(academyReceiptMatches(receipt, 'request-one', 'my-project')).toBe(true)
  expect(academyReceiptMatches(receipt, 'request-one', 'other-project')).toBe(false)
  receipt.result.submission!.request_id = 'other-request'
  expect(academyReceiptMatches(receipt, 'request-one', 'my-project')).toBe(false)
})

it('keeps missing observations distinct from an observed zero', () => {
  const dimensions = academyDimensions([{id: 'concept', score: 0, reason: 'Contradiction', evidence: [], next_step: 'Read again'}])
  expect(dimensions[0].dimension?.score).toBe(0)
  expect(dimensions[1].dimension).toBeUndefined()
  expect(academyRadarPoint(0, 0)).toEqual({x: 180, y: 150})
  expect(academyRadarPoint(0, 5)).toEqual({x: 180, y: 64})
})

it('renders localized five-dimension evidence without translating generated feedback', async () => {
  const saved = submission({status: 'completed', assessment: {
    summary: 'Original feedback 原文',
    dimensions: [
      {id: 'concept', score: 0, reason: 'Original reason', evidence: [{source: 'agent.py', quote: 'original_code()'}], next_step: 'Original next step'},
      {id: 'goal', score: 5, reason: 'Implemented', evidence: [], next_step: 'Try another input'},
      {id: 'reasoning', score: 3, reason: 'Explained', evidence: [], next_step: 'Explain a tradeoff'},
      {id: 'verification', score: null, reason: 'No evidence yet', evidence: [], next_step: 'Write a check'},
      {id: 'reflection', score: 2, reason: 'Partial', evidence: [{source: 'note', quote: 'My prediction'}], next_step: 'Describe a failure'},
    ], next_steps: ['Keep the original advice'],
  }})
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    const html = renderToStaticMarkup(createElement(AcademyAssessment, {submission: saved}))
    expect(html).toContain(t('ui.academy.radarLabel'))
    expect(html).toContain(t('ui.academy.dimension.concept'))
    expect(html).toContain(t('ui.academy.notObserved'))
    expect(html).toContain(t('ui.academy.score', {score: 0}))
    expect(html).toContain('0/5')
    expect(html).toContain('data-dimension="concept"')
    expect(html).not.toContain('data-dimension="verification"')
    expect(html).toContain('original_code()')
    expect(html).toContain('Original feedback 原文')
    expect(html).toContain('original-model')
    const rubric = renderToStaticMarkup(createElement(AcademyRubric))
    for (const score of [0, 1, 2, 3, 4, 5]) expect(rubric).toContain(t('ui.academy.rubric.' + score).replaceAll("'", '&#x27;'))
    expect(rubric).toContain(t('ui.academy.rubricIntro'))
  }
})

it.each(['failed', 'unknown'] as const)('preserves %s submissions and their diagnostic', status => {
  const html = renderToStaticMarkup(createElement(AcademyAssessment, {submission: submission({status, error_code: 'provider/original-error'})}))
  expect(html).toContain(t(status === 'unknown' ? 'ui.academy.assessmentUnknown' : 'ui.academy.assessmentFailed'))
  expect(html).toContain('provider/original-error')
  expect(html).not.toContain('<svg')
})

it('updates actionable assessment errors when the interface language changes', async () => {
  const saved = submission({status: 'failed', error_code: 'assessment-unavailable'})
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    const html = renderToStaticMarkup(createElement(AcademyAssessment, {submission: saved}))
    expect(html).toContain(t('ui.academy.error.assessment-unavailable'))
    expect(html).toContain('assessment-unavailable')
  }
})

it('polls only while a submission is evaluating, without overlapping reads', async () => {
  vi.useFakeTimers()
  const read = vi.fn().mockResolvedValueOnce(response([submission()])).mockResolvedValueOnce(response([submission({status: 'completed'})]))
  const publish = vi.fn()
  const stop = watchAcademySubmissions('my-project', {read, publish, failure: vi.fn()})
  await vi.advanceTimersByTimeAsync(0)
  expect(read).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(2000)
  expect(read).toHaveBeenCalledTimes(2)
  expect(publish).toHaveBeenLastCalledWith([submission({status: 'completed'})])
  await vi.advanceTimersByTimeAsync(10000)
  expect(read).toHaveBeenCalledTimes(2)
  stop()
})

it('rejects late reads after hiding or switching projects even when transport ignores abort', async () => {
  vi.useFakeTimers()
  let resolveOld!: (value: ApiResult<{submissions: AcademySubmission[]}>) => void
  const oldRead = vi.fn(() => new Promise<ApiResult<{submissions: AcademySubmission[]}>>(resolve => {resolveOld = resolve}))
  const oldPublish = vi.fn()
  const stop = watchAcademySubmissions('old-project', {read: oldRead, publish: oldPublish, failure: vi.fn(), pending: true})
  await vi.advanceTimersByTimeAsync(10000)
  expect(oldRead).toHaveBeenCalledTimes(1)
  stop()
  const publish = vi.fn()
  const stopNew = watchAcademySubmissions('my-project', {read: vi.fn().mockResolvedValue(response([submission({status: 'unknown'})])), publish, failure: vi.fn()})
  resolveOld(response([submission({project_id: 'old-project'})]))
  await vi.advanceTimersByTimeAsync(0)
  expect(oldPublish).not.toHaveBeenCalled()
  expect(publish).toHaveBeenCalledWith([submission({status: 'unknown'})])
  await vi.advanceTimersByTimeAsync(10000)
  expect(oldRead).toHaveBeenCalledTimes(1)
  stopNew()
})

it('rejects another project in a list and never turns it into the current feedback', async () => {
  vi.useFakeTimers()
  const publish = vi.fn(), failure = vi.fn()
  const stop = watchAcademySubmissions('my-project', {read: vi.fn().mockResolvedValue(response([submission({project_id: 'other'})])), publish, failure})
  await vi.advanceTimersByTimeAsync(0)
  expect(publish).not.toHaveBeenCalled()
  expect(failure).toHaveBeenCalledWith(expect.objectContaining({code: 'response/invalid-schema'}))
  expect(vi.getTimerCount()).toBe(0)
  stop()
})

it('does not poll an unknown outcome or retry an explicitly denied observation', async () => {
  vi.useFakeTimers()
  const denied = vi.fn().mockResolvedValue({ok: false, status: 403, code: 'forbidden', message: 'Denied'})
  const stop = watchAcademySubmissions('my-project', {read: denied, publish: vi.fn(), failure: vi.fn(), pending: true})
  await vi.advanceTimersByTimeAsync(10000)
  expect(denied).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
  stop()
})

it('submits the saved snapshot identity and note without executing code first', async () => {
  const receipt: AcademyReceipt = {request_id: 'request-one', status: 'ready', result: {submission: submission()}}
  const fetch = vi.fn().mockResolvedValue(Response.json(receipt))
  vi.stubGlobal('fetch', fetch)
  const body = {request_id: 'request-one', lesson_id: 'day-01', expected_fingerprint: 'a'.repeat(64), note: 'Prediction and verification 原文'}
  const result = await academyApi.submit('my-project', body)
  expect(result.ok).toBe(true)
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(fetch.mock.calls[0][0]).toBe('/api/v1/academy/projects/my-project/submissions')
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(body)
})

const modelRun = (patch: Partial<AcademyModelRun> = {}): AcademyModelRun => ({
  id: 'model-one', project_id: 'my-project', lesson_id: 'model-lesson', request_id: 'request-one', input_fingerprint: 'a'.repeat(64),
  system: 'Frozen system', prompt: '原始提示', response: '', model: 'original-model', language: 'en', status: 'running', created_at: 1, ...patch,
})

it('keeps domain outcome-unknown responses pending even when the HTTP status is 409', () => {
  const failure = {ok: false as const, status: 409, code: 'academy/outcome-unknown', message: 'Original unknown receipt'}
  expect(isAcademyUnconfirmedWrite(failure)).toBe(true)
  expect(isAcademyUnconfirmedWrite({...failure, code: 'academy/stale-project'})).toBe(false)
  expect(isAcademyUnconfirmedWrite({...failure, status: 200, code: 'response/invalid-schema'})).toBe(true)
  expect(isAcademyUnconfirmedWrite({...failure, status: 503, code: 'academy/store-invalid'})).toBe(true)
})

it('validates model receipt ownership and sends the original prompt through the generated response contract', async () => {
  const receipt: AcademyReceipt = {request_id: 'request-one', status: 'ready', result: {model_run: modelRun()}}
  expect(academyReceiptMatches(receipt, 'request-one', 'my-project')).toBe(true)
  expect(academyReceiptMatches(receipt, 'request-one', 'other-project')).toBe(false)
  expect(academyReceiptMatches({...receipt, result: {model_run: modelRun({request_id: 'another'})}}, 'request-one', 'my-project')).toBe(false)
  const fetch = vi.fn().mockResolvedValue(Response.json(receipt))
  vi.stubGlobal('fetch', fetch)
  const body = {request_id: 'request-one', lesson_id: 'model-lesson', expected_fingerprint: 'a'.repeat(64), prompt: '用户原文 / {prompt}'}
  expect((await academyApi.modelRun('my-project', body)).ok).toBe(true)
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(fetch.mock.calls[0][0]).toBe('/api/v1/academy/projects/my-project/model-runs')
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(body)
})

it('reads a provisional model answer through the generated contract and rejects malformed draft fields', async () => {
  const snapshot = {model_runs: [modelRun({partial_response: '正在生成 / first part'})]}
  const fetch = vi.fn().mockResolvedValueOnce(Response.json(snapshot))
    .mockResolvedValueOnce(Response.json({model_runs: [modelRun({status: 'completed', response: 'Final / 完整回答', completed_at: 2})]}))
    .mockResolvedValueOnce(Response.json({model_runs: [{...modelRun(), partial_response: 7}]}))
  vi.stubGlobal('fetch', fetch)
  expect(await academyApi.modelRuns('my-project')).toEqual({ok: true, data: snapshot})
  const final = await academyApi.modelRuns('my-project')
  expect(final.ok && final.data.model_runs[0].response).toBe('Final / 完整回答')
  expect(final.ok && final.data.model_runs[0].partial_response).toBeUndefined()
  expect(await academyApi.modelRuns('my-project')).toMatchObject({ok: false, code: 'response/invalid-schema'})
  expect(fetch).toHaveBeenCalledTimes(3)
  expect(fetch.mock.calls.every(([, options]) => !options?.method || options.method === 'GET')).toBe(true)
})

it('keeps the stop receipt separate from the original model request', async () => {
  const receipt: AcademyReceipt = {request_id: 'stop-one', status: 'ready', result: {model_run: modelRun({stopping: true})}}
  expect(academyReceiptMatches(receipt, 'stop-one', 'my-project')).toBe(false)
  expect(academyReceiptMatches(receipt, 'stop-one', 'my-project', undefined, 'request-one')).toBe(true)
  expect(academyReceiptMatches(receipt, 'stop-one', 'other-project', undefined, 'request-one')).toBe(false)
  const fetch = vi.fn().mockResolvedValue(Response.json(receipt))
  vi.stubGlobal('fetch', fetch)
  expect(await academyApi.stopModel('my-project', 'model-one', {request_id: 'stop-one'})).toEqual({ok: true, data: receipt})
  expect(fetch.mock.calls[0][0]).toBe('/api/v1/academy/projects/my-project/model-runs/model-one/stop')
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({request_id: 'stop-one'})
})

it('serializes experiment observations, cancels hidden reads and stops at unconfirmed outcomes', async () => {
  vi.useFakeTimers()
  let resolve!: (value: ApiResult<{model_runs: AcademyModelRun[]}>) => void
  let signal: AbortSignal | undefined
  const read = vi.fn((_project: string, abort?: AbortSignal) => {
    signal = abort
    return new Promise<ApiResult<{model_runs: AcademyModelRun[]}>>(done => {resolve = done})
  })
  const publish = vi.fn(), failure = vi.fn()
  const stop = watchAcademyModelRuns('my-project', {read, publish, failure, pending: true})
  await vi.advanceTimersByTimeAsync(10000)
  expect(read).toHaveBeenCalledTimes(1)
  stop()
  expect(signal?.aborted).toBe(true)
  resolve({ok: true, data: {model_runs: [modelRun()]}})
  await vi.advanceTimersByTimeAsync(10000)
  expect(publish).not.toHaveBeenCalled()
  expect(failure).not.toHaveBeenCalled()
  const readNew = vi.fn().mockResolvedValueOnce({ok: true, data: {model_runs: [modelRun()]}})
    .mockResolvedValueOnce({ok: true, data: {model_runs: [modelRun({status: 'unknown', error_code: 'model-interrupted'})]}})
  const stopNew = watchAcademyModelRuns('my-project', {read: readNew, publish, failure})
  await vi.advanceTimersByTimeAsync(10000)
  expect(readNew).toHaveBeenCalledTimes(2)
  expect(publish).toHaveBeenLastCalledWith([modelRun({status: 'unknown', error_code: 'model-interrupted'})])
  stopNew()
})

it('rejects experiment results belonging to another project before publishing', async () => {
  vi.useFakeTimers()
  const read = vi.fn().mockResolvedValue({ok: true, data: {model_runs: [modelRun({project_id: 'other'})]}})
  const publish = vi.fn(), failure = vi.fn()
  const stop = watchAcademyModelRuns('my-project', {read, publish, failure})
  await vi.advanceTimersByTimeAsync(0)
  expect(publish).not.toHaveBeenCalled()
  expect(failure).toHaveBeenCalledWith(expect.objectContaining({code: 'response/invalid-schema'}))
  expect(vi.getTimerCount()).toBe(0)
  stop()
})

it('shows model lesson completion as a learning activity rather than a Python check', async () => {
  const course = {...learningCourse, lessons: [{...lesson('model'), mode: 'model' as const}]}
  const project = {...learningProject, progress: [{lesson_id: 'model', state: 'completed' as const, checkpoint_id: 'checkpoint', evidence_ids: ['experiment'], attempt_count: 1}]}
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    const html = renderToStaticMarkup(createElement(AcademyLearningPath, {course, project, lessonId: 'model', disabled: false, onSelect: () => {}}))
    expect(html).toContain(t('ui.academy.studyCompleted'))
    expect(html).not.toContain(t('ui.academy.checkPassed'))
  }
})

it('keeps API error identity across language changes without repeating the failed model request', async () => {
  await setLocale('en')
  const fetch = vi.fn().mockResolvedValue(Response.json({code: 'academy/model-busy', error: t('ui.academy.error.model-busy')}, {status: 400}))
  vi.stubGlobal('fetch', fetch)
  const result = await academyApi.modelRun('my-project', {request_id: 'request', lesson_id: 'model', expected_fingerprint: 'a'.repeat(64), prompt: 'User prompt'})
  expect(result.ok).toBe(false)
  if (result.ok) throw Error('Expected a rejected experiment')
  expect(result.copy).toEqual({key: 'ui.academy.error.model-busy'})
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    expect(noticeText(result.copy!)).toBe(t('ui.academy.error.model-busy'))
    expect(fetch).toHaveBeenCalledTimes(1)
  }
  fetch.mockResolvedValue(Response.json({code: 'academy/unknown-external', error: 'Original provider error'}, {status: 400}))
  const external = await academyApi.modelRuns('my-project')
  expect(external).toMatchObject({ok: false, message: 'Original provider error'})
  if (!external.ok) expect(external.copy).toBeUndefined()
})
