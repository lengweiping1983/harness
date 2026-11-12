import {isUnconfirmedWrite, request as httpRequest} from './http'
import {hasTranslation} from '@/i18n'
import type { AcademyAssessmentDimension, AcademyCheckpoint, AcademyCourse, AcademyCourseSummary, AcademyDesign, AcademyDimensionId, AcademyFile, AcademyLesson, AcademyModelRun, AcademyProject, AcademyReceipt, AcademyRestorePreview, AcademyRun, AcademySourceFile, AcademySubmission, AcademyTutorTurn } from '@/types'
import type {ApiResult, RequestOptions} from './http'
import type { AcademyCompleteStudy, AcademyCreateDesign, AcademyCreateProject, AcademyExecute, AcademyModelRunRequest, AcademyRestore, AcademySaveCheckpoint, AcademyStartLesson, AcademyWriteFile, AcademySubmit, AcademyTutorRequest, AcademyTutorStop, AcademyModelStop } from './protocol.generated'

const prefix = '/api/v1/academy'
const id = encodeURIComponent
/** 已知的领域未知回执即便使用 409，仍只能回查，不能按明确拒绝释放准入。 */
export function isAcademyUnconfirmedWrite(failure: Exclude<ApiResult<unknown>, {ok: true}>): boolean {
  return failure.code === 'academy/outcome-unknown' || isUnconfirmedWrite(failure)
}

/** Academy 自有错误按机器码保留资源身份，语言切换不重新提交请求。 */
async function request<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
  const result = await httpRequest<T>(path, options)
  if (!result.ok && result.code.startsWith('academy/')) {
    const key = 'ui.academy.error.' + result.code.slice('academy/'.length)
    if (hasTranslation(key)) return {...result, copy: {key}}
  }
  return result
}
const post = (path: string, body: unknown) => request<AcademyReceipt>(prefix + path, {method: 'POST', body: JSON.stringify(body), timeout: 60_000})
export const academyApi = {
  courses: (signal?: AbortSignal) => request<{courses: AcademyCourseSummary[]}>(prefix + '/courses', {signal}),
  course: (course: string, signal?: AbortSignal) => request<{course: AcademyCourse}>(prefix + '/courses/' + id(course), {signal}),
  design: (body: AcademyCreateDesign) => post('/designs', body),
  designs: (signal?: AbortSignal) => request<{designs: AcademyDesign[]}>(prefix + '/designs', {signal}),
  projects: (signal?: AbortSignal) => request<{projects: AcademyProject[]}>(prefix + '/projects', {signal}),
  create: (body: AcademyCreateProject) => post('/projects', body),
  project: (project: string, signal?: AbortSignal) => request<{project: AcademyProject}>(prefix + '/projects/' + id(project), {signal}),
  files: (project: string, signal?: AbortSignal) => request<{files: AcademyFile[]}>(prefix + '/projects/' + id(project) + '/files', {signal}),
  source: (project: string, path: string, signal?: AbortSignal) => request<AcademySourceFile>(prefix + '/projects/' + id(project) + '/source?path=' + id(path), {signal}),
  reference: (course: string, lesson: string, path: string, signal?: AbortSignal) => request<AcademySourceFile>(prefix + '/courses/' + id(course) + '/lessons/' + id(lesson) + '/reference?path=' + id(path), {signal}),
  start: (project: string, lesson: string, body: AcademyStartLesson) => post('/projects/' + id(project) + '/lessons/' + id(lesson) + '/start', body),
  save: (project: string, body: AcademyWriteFile) => post('/projects/' + id(project) + '/files', body),
  run: (project: string, body: AcademyExecute, check = false) => post('/projects/' + id(project) + (check ? '/check' : '/run'), body),
  complete: (project: string, body: AcademyCompleteStudy) => post('/projects/' + id(project) + '/complete', body),
  modelRun: (project: string, body: AcademyModelRunRequest) => post('/projects/' + id(project) + '/model-runs', body),
  stopModel: (project: string, run: string, body: AcademyModelStop) => post('/projects/' + id(project) + '/model-runs/' + id(run) + '/stop', body),
  modelRuns: (project: string, signal?: AbortSignal) => request<{model_runs: AcademyModelRun[]}>(prefix + '/projects/' + id(project) + '/model-runs', {signal}),
  tutor: (project: string, body: AcademyTutorRequest) => post('/projects/' + id(project) + '/tutor-turns', body),
  stopTutor: (project: string, turn: string, body: AcademyTutorStop) => post('/projects/' + id(project) + '/tutor-turns/' + id(turn) + '/stop', body),
  tutorTurns: (project: string, signal?: AbortSignal) => request<{tutor_turns: AcademyTutorTurn[]}>(prefix + '/projects/' + id(project) + '/tutor-turns', {signal}),
  checkpoints: (project: string, signal?: AbortSignal) => request<{checkpoints: AcademyCheckpoint[]}>(prefix + '/projects/' + id(project) + '/checkpoints', {signal}),
  checkpoint: (project: string, body: AcademySaveCheckpoint) => post('/projects/' + id(project) + '/checkpoints', body),
  preview: (project: string, checkpoint: string, signal?: AbortSignal) => request<AcademyRestorePreview>(prefix + '/projects/' + id(project) + '/restore?checkpoint_id=' + id(checkpoint), {signal}),
  restore: (project: string, body: AcademyRestore) => post('/projects/' + id(project) + '/restore', body),
  runs: (project: string, signal?: AbortSignal) => request<{runs: AcademyRun[]}>(prefix + '/projects/' + id(project) + '/runs', {signal}),
  submit: (project: string, body: AcademySubmit) => post('/projects/' + id(project) + '/submissions', body),
  submissions: (project: string, signal?: AbortSignal) => request<{submissions: AcademySubmission[]}>(prefix + '/projects/' + id(project) + '/submissions', {signal}),
  submission: (project: string, submission: string, signal?: AbortSignal) => request<{submission: AcademySubmission}>(prefix + '/projects/' + id(project) + '/submissions/' + id(submission), {signal}),
  receipt: (requestId: string, signal?: AbortSignal) => request<AcademyReceipt>(prefix + '/receipts/' + id(requestId), {signal}),
}

export function academyReceiptMatches(receipt: AcademyReceipt, requestId: string, projectId?: string, tutorRequestId = requestId, modelRequestId = requestId): boolean {
  if (receipt.request_id !== requestId) return false
  if (projectId && [receipt.result.project?.id, receipt.result.run?.project_id, receipt.result.checkpoint?.project_id, receipt.result.submission?.project_id, receipt.result.model_run?.project_id, receipt.result.tutor_turn?.project_id].some(id => id !== undefined && id !== projectId)) return false
  if (receipt.result.submission && receipt.result.submission.request_id !== requestId) return false
  if (receipt.result.design && receipt.result.design.request_id !== requestId) return false
  if (receipt.result.model_run && receipt.result.model_run.request_id !== modelRequestId) return false
  if (receipt.result.tutor_turn && receipt.result.tutor_turn.request_id !== tutorRequestId) return false
  if (receipt.status === 'ready' && !receipt.result.project && !receipt.result.checkpoint && !receipt.result.submission && !receipt.result.design && !receipt.result.model_run && !receipt.result.tutor_turn) return false
  if (receipt.status === 'failed' && !receipt.result.error_code) return false
  return true
}

export const academyDimensionIds: readonly AcademyDimensionId[] = ['concept', 'goal', 'reasoning', 'verification', 'reflection']

/** 当前项目优先；否则返回同版本课程中最新创建的项目，不改写任何学习记录。 */
export function academyCourseProject(course: Pick<AcademyCourseSummary, 'id' | 'version'>, projects: readonly AcademyProject[], preferredId: string): AcademyProject | undefined {
  const matches = projects.filter(project => project.course_id === course.id && project.course_version === course.version)
  return matches.find(project => project.id === preferredId) ?? matches.reduce<AcademyProject | undefined>((latest, project) =>
    !latest || project.created_at > latest.created_at ? project : latest, undefined)
}

/** 没有证据的维度保持空值，不能投影为零分。 */
export function academyDimensions(dimensions: readonly AcademyAssessmentDimension[]): Array<{id: AcademyDimensionId; dimension?: AcademyAssessmentDimension}> {
  return academyDimensionIds.map(id => ({id, dimension: dimensions.find(dimension => dimension.id === id)}))
}

export function academyRadarPoint(index: number, score: number, radius = 86): {x: number; y: number} {
  const angle = -Math.PI / 2 + index * Math.PI * 2 / academyDimensionIds.length
  return {x: 180 + Math.cos(angle) * radius * score / 5, y: 150 + Math.sin(angle) * radius * score / 5}
}

/** 一次观察只拥有自己的项目和取消状态，旧请求即使忽略 AbortSignal 也不能发布。 */
export function watchAcademySubmissions(projectId: string, options: {
  publish: (submissions: AcademySubmission[]) => void
  failure: (failure: Exclude<ApiResult<unknown>, {ok: true}>) => void
  read?: typeof academyApi.submissions
  interval?: number
  pending?: boolean
}): () => void {
  return watchAcademyRecords(projectId, {...options, pendingStatus: 'evaluating', read: async signal => {
    const result = await (options.read ?? academyApi.submissions)(projectId, signal)
    return result.ok ? {...result, data: result.data.submissions} : result
  }})
}

export function watchAcademyModelRuns(projectId: string, options: {
  publish: (runs: AcademyModelRun[]) => void
  failure: (failure: Exclude<ApiResult<unknown>, {ok: true}>) => void
  read?: typeof academyApi.modelRuns
  interval?: number
  pending?: boolean
}): () => void {
  return watchAcademyRecords(projectId, {...options, pendingStatus: 'running', read: async signal => {
    const result = await (options.read ?? academyApi.modelRuns)(projectId, signal)
    return result.ok ? {...result, data: result.data.model_runs} : result
  }})
}

export function watchAcademyTutorTurns(projectId: string, options: {
  publish: (turns: AcademyTutorTurn[]) => void
  failure: (failure: Exclude<ApiResult<unknown>, {ok: true}>) => void
  read?: typeof academyApi.tutorTurns
  interval?: number
  pending?: boolean
}): () => void {
  return watchAcademyRecords(projectId, {...options, pendingStatus: 'running', read: async signal => {
    const result = await (options.read ?? academyApi.tutorTurns)(projectId, signal)
    return result.ok ? {...result, data: result.data.tutor_turns} : result
  }})
}

function watchAcademyRecords<T extends {project_id: string; status: string}>(projectId: string, options: {
  publish: (rows: T[]) => void
  failure: (failure: Exclude<ApiResult<unknown>, {ok: true}>) => void
  read: (signal: AbortSignal) => Promise<ApiResult<T[]>>
  pendingStatus: string
  interval?: number
  pending?: boolean
}): () => void {
  let disposed = false
  let evaluating = options.pending ?? false
  let controller: AbortController | undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  const refresh = async () => {
    controller = new AbortController()
    const result = await options.read(controller.signal)
    if (disposed) return
    if (result.ok) {
      if (result.data.some(row => row.project_id !== projectId)) {
        options.failure({ok: false, status: 200, code: 'response/invalid-schema', message: '', copy: {key: 'ui.http.invalidResponse'}})
        return
      }
      options.publish(result.data)
      evaluating = result.data.some(row => row.status === options.pendingStatus)
    } else {
      options.failure(result)
      if (result.status >= 400 && result.status < 500 && result.status !== 408 && result.status !== 429) evaluating = false
    }
    if (evaluating && !disposed) timer = setTimeout(() => void refresh(), options.interval ?? 2000)
  }
  void refresh()
  return () => {disposed = true; clearTimeout(timer); controller?.abort()}
}

export function academyProgressPercent(course: Pick<AcademyCourse, 'lessons'>, project: Pick<AcademyProject, 'progress'>): number {
  if (!course.lessons.length) return 0
  return Math.round(course.lessons.filter(lesson => project.progress.some(row => row.lesson_id === lesson.id && row.state !== 'in_progress')).length / course.lessons.length * 100)
}

/** 相邻同阶段课次组成一段；首课身份稳定，不用译文作为界面身份。 */
export function academyStages(course: Pick<AcademyCourse, 'lessons'>): Array<{id: string; title?: string; goal?: string; lessons: AcademyLesson[]}> {
  const stages: Array<{id: string; title?: string; goal?: string; lessons: AcademyLesson[]}> = []
  for (const lesson of course.lessons) {
    const previous = stages.at(-1)
    if (previous && previous.title === lesson.stage) previous.lessons.push(lesson)
    else stages.push({id: lesson.id, title: lesson.stage, ...(lesson.stage_goal ? {goal: lesson.stage_goal} : {}), lessons: [lesson]})
  }
  return stages
}

/** 继续原来未完成的一课；已检查通过时再推荐未完成课，不自动执行或重置文件。 */
export function academyResumeLesson(course: AcademyCourse, project: AcademyProject): AcademyLesson | undefined {
  if (course.id !== project.course_id) return undefined
  const passed = new Set(project.progress.filter(row => row.state !== 'in_progress').map(row => row.lesson_id))
  return course.lessons.find(lesson => lesson.id === project.active_lesson_id && !passed.has(lesson.id))
    ?? course.lessons.find(lesson => !passed.has(lesson.id))
}

/** 只有当前项目的明确活动证据才能开放继续入口；历史代码通过不能确认新输入。 */
export function academyActiveLessonCompleted(course: AcademyCourse, project: AcademyProject, runs: readonly AcademyRun[]): boolean {
  if (course.id !== project.course_id || course.version !== project.course_version) return false
  const lesson = course.lessons.find(item => item.id === project.active_lesson_id)
  const progress = project.progress.find(item => item.lesson_id === project.active_lesson_id)
  if (!lesson || !progress?.evidence_ids.length) return false
  if (lesson.mode !== 'python') return progress.state === 'completed'
  return progress.state === 'passed' && runs.some(run => progress.evidence_ids.includes(run.id)
    && run.project_id === project.id && run.lesson_id === lesson.id && run.kind === 'check'
    && run.input_fingerprint === project.fingerprint && !run.input_changed
    && run.result.status === 'passed' && run.result.exit_code === 0)
}

export function academyLessonRun(runs: readonly AcademyRun[], projectId: string, lessonId: string): AcademyRun | undefined {
  return runs.find(run => run.project_id === projectId && run.lesson_id === lessonId)
}
