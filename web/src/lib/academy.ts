import {isUnconfirmedWrite, request as httpRequest} from './http'
import {hasTranslation} from '@/i18n'
import type { AcademyAssessmentDimension, AcademyCheckpoint, AcademyCourse, AcademyCourseSummary, AcademyDesign, AcademyDimensionId, AcademyFile, AcademyLesson, AcademyModelRun, AcademyProject, AcademyReceipt, AcademyRestorePreview, AcademyRun, AcademySourceFile, AcademySubmission, AcademyTutorTurn, AcademyMastery, AcademyReviewItem, AcademyTeachQuiz, AcademyTeachMisconception, AcademyStruggleView, AcademyWorldView, AcademyStepsView } from '@/types'
import type {ApiResult, RequestOptions} from './http'
import type { AcademyCompleteStudy, AcademyCreateDesign, AcademyCreateProject, AcademyExecute, AcademyModelRunRequest, AcademyRestore, AcademySaveCheckpoint, AcademyStartLesson, AcademyWriteFile, AcademySubmit, AcademyTutorRequest, AcademyTutorStop, AcademyModelStop, AcademyReviewRequest, AcademyTeachQuizRequest, AcademyStruggleStart, AcademyStruggleAttempt, AcademyStruggleReveal, AcademyWorldStart, AcademyWorldPredict, AcademyWorldObserve, AcademyWorldComplete, AcademyStepAnswer } from './protocol.generated'

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
  mastery: (project: string, signal?: AbortSignal) => request<{mastery: AcademyMastery}>(prefix + '/projects/' + id(project) + '/mastery', {signal}),
  reviews: (project: string, signal?: AbortSignal) => request<{reviews: AcademyReviewItem[]}>(prefix + '/projects/' + id(project) + '/reviews', {signal}),
  cumulative: (project: string, signal?: AbortSignal) => request<{reviews: AcademyReviewItem[]}>(prefix + '/projects/' + id(project) + '/cumulative', {signal}),
  review: (project: string, body: AcademyReviewRequest) => post('/projects/' + id(project) + '/reviews', body),
  teachQuiz: (project: string, turn: string, body: AcademyTeachQuizRequest) => post('/projects/' + id(project) + '/tutor-turns/' + id(turn) + '/quiz', body),
  teachQuizzes: (project: string, signal?: AbortSignal) => request<{quizzes: AcademyTeachQuiz[]}>(prefix + '/projects/' + id(project) + '/teach-quizzes', {signal}),
  struggle: (project: string, lesson: string, signal?: AbortSignal) => request<AcademyStruggleView>(prefix + '/projects/' + id(project) + '/struggle?lesson_id=' + id(lesson), {signal}),
  struggleStart: (project: string, body: AcademyStruggleStart) => post('/projects/' + id(project) + '/struggle', body),
  struggleAttempt: (project: string, body: AcademyStruggleAttempt) => post('/projects/' + id(project) + '/struggle/attempt', body),
  struggleReveal: (project: string, body: AcademyStruggleReveal) => post('/projects/' + id(project) + '/struggle/reveal', body),
  world: (project: string, lesson: string, signal?: AbortSignal) => request<AcademyWorldView>(prefix + '/projects/' + id(project) + '/world?lesson_id=' + id(lesson), {signal}),
  worldStart: (project: string, body: AcademyWorldStart) => post('/projects/' + id(project) + '/world', body),
  worldPredict: (project: string, body: AcademyWorldPredict) => post('/projects/' + id(project) + '/world/predict', body),
  worldObserve: (project: string, body: AcademyWorldObserve) => post('/projects/' + id(project) + '/world/observe', body),
  worldComplete: (project: string, body: AcademyWorldComplete) => post('/projects/' + id(project) + '/world/complete', body),
  steps: (project: string, lesson: string, signal?: AbortSignal) => request<AcademyStepsView>(prefix + '/projects/' + id(project) + '/steps?lesson_id=' + id(lesson), {signal}),
  stepAnswer: (project: string, body: AcademyStepAnswer) => post('/projects/' + id(project) + '/steps/answer', body),
  receipt: (requestId: string, signal?: AbortSignal) => request<AcademyReceipt>(prefix + '/receipts/' + id(requestId), {signal}),
}

export function academyReceiptMatches(receipt: AcademyReceipt, requestId: string, projectId?: string, tutorRequestId = requestId, modelRequestId = requestId): boolean {
  if (receipt.request_id !== requestId) return false
  if (projectId && [receipt.result.project?.id, receipt.result.run?.project_id, receipt.result.checkpoint?.project_id, receipt.result.submission?.project_id, receipt.result.model_run?.project_id, receipt.result.tutor_turn?.project_id, receipt.result.review?.project_id, receipt.result.quiz?.project_id, receipt.result.struggle?.project_id, receipt.result.world?.project_id].some(id => id !== undefined && id !== projectId)) return false
  if (receipt.result.submission && receipt.result.submission.request_id !== requestId) return false
  if (receipt.result.design && receipt.result.design.request_id !== requestId) return false
  if (receipt.result.review && receipt.result.review.request_id !== requestId) return false
  if (receipt.result.quiz && receipt.result.quiz.request_id !== requestId) return false
  if (receipt.result.model_run && receipt.result.model_run.request_id !== modelRequestId) return false
  if (receipt.result.tutor_turn && receipt.result.tutor_turn.request_id !== tutorRequestId) return false
  if (receipt.status === 'ready' && !receipt.result.project && !receipt.result.checkpoint && !receipt.result.submission && !receipt.result.design && !receipt.result.model_run && !receipt.result.tutor_turn && !receipt.result.review && !receipt.result.quiz && !receipt.result.struggle && !receipt.result.world && !receipt.result.step) return false
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

/** 重测选择必须覆盖每道题且选项真实存在；不完整时不允许提交。 */
export function academyReviewComplete(item: Pick<AcademyReviewItem, 'checks'>, selections: Readonly<Record<string, string>>): boolean {
  return item.checks.length > 0 && item.checks.every(check => check.options.some(option => option.id === selections[check.id]))
}

/** 按课程题目身份组装答案，不携带界面译文。 */
export function academyReviewAnswers(item: Pick<AcademyReviewItem, 'checks'>, selections: Readonly<Record<string, string>>): AcademyReviewRequest['answers'] {
  return item.checks.map(check => ({check_id: check.id, option_id: selections[check.id]}))
}

/** 记忆仪表盘计数；没有证据的技能保持未开始，不投影为已掌握。 */
export type AcademyBadge = 'repairer' | 'transferer' | 'consistent' | 'master'

/** 证据徽章：只由真实学习证据触发（修复/迁移/连续/牢固），非登录或点击等虚荣指标。 */
export function academyBadges(mastery: Pick<AcademyMastery, 'repairs' | 'transfers' | 'streak_days' | 'skills'>): AcademyBadge[] {
  const badges: AcademyBadge[] = []
  if ((mastery.repairs ?? 0) > 0) badges.push('repairer')
  if ((mastery.transfers ?? 0) > 0) badges.push('transferer')
  if ((mastery.streak_days ?? 0) >= 3) badges.push('consistent')
  if (mastery.skills.filter(skill => skill.state === 'durable').length >= 3) badges.push('master')
  return badges
}

export function academyMasteryCounts(mastery: Pick<AcademyMastery, 'skills'>): {durable: number; learning: number; fresh: number} {
  return {durable: mastery.skills.filter(skill => skill.state === 'durable').length,
    learning: mastery.skills.filter(skill => skill.state === 'learning').length,
    fresh: mastery.skills.filter(skill => skill.state === 'new').length}
}

/** 教 AI 的误解预览与宿主冻结规则一致：首个含错误选项的自测、首个错误选项。 */
export function academyTeachMisconception(lesson: Pick<AcademyLesson, 'knowledge_checks'>): AcademyTeachMisconception | undefined {
  for (const check of lesson.knowledge_checks ?? []) {
    const wrong = check.options.find(option => option.id !== check.answer_id)
    if (wrong) return {check_id: check.id, option_id: wrong.id, prompt: check.prompt, label: wrong.label, feedback: wrong.feedback}
  }
  return undefined
}

/** 本课已完成或进行中的教学对话，按时间正序；普通带练不混入。 */
export function academyTeachTurns(turns: readonly AcademyTutorTurn[], lessonId: string): AcademyTutorTurn[] {
  return turns.filter(turn => turn.mode === 'teach' && turn.lesson_id === lessonId).sort((a, b) => a.created_at - b.created_at)
}

export function watchAcademyTeachQuizzes(projectId: string, options: {
  publish: (quizzes: AcademyTeachQuiz[]) => void
  failure: (failure: Exclude<ApiResult<unknown>, {ok: true}>) => void
  read?: typeof academyApi.teachQuizzes
  interval?: number
  pending?: boolean
}): () => void {
  return watchAcademyRecords(projectId, {...options, pendingStatus: 'running', read: async signal => {
    const result = await (options.read ?? academyApi.teachQuizzes)(projectId, signal)
    return result.ok ? {...result, data: result.data.quizzes} : result
  }})
}

/** 微世界参数控件规格；宿主内核仍是唯一真值与边界来源。 */
export const academyWorldParams: Record<string, Array<{key: string; min: number; max: number}>> = {
  'fraction-bar': [{key: 'parts', min: 2, max: 12}, {key: 'shaded', min: 0, max: 12}],
  'group-array': [{key: 'groups', min: 1, max: 9}, {key: 'per_group', min: 1, max: 9}],
  'balance-scale': [{key: 'left', min: 0, max: 100}, {key: 'right', min: 0, max: 100}],
  'float-sink': [{key: 'object', min: 1, max: 20}, {key: 'liquid', min: 1, max: 20}],
}

/** 自适应步骤路由：答错主步骤时路由到 on_wrong 支架步骤，支架掌握后回到主步骤；支架步骤不计入完成。 */
export function academyNextStep<T extends {id: string; on_wrong?: string}>(steps: readonly T[], responses: ReadonlyArray<{step_id: string; correct: boolean}>): T | null {
  const supportIds = new Set(steps.map(step => step.on_wrong).filter((id): id is string => Boolean(id)))
  const correct = new Set(responses.filter(response => response.correct).map(response => response.step_id))
  const wrong = new Set(responses.filter(response => !response.correct).map(response => response.step_id))
  for (const step of steps) {
    if (supportIds.has(step.id) || correct.has(step.id)) continue
    if (step.on_wrong && wrong.has(step.id) && !correct.has(step.on_wrong)) {
      const support = steps.find(item => item.id === step.on_wrong)
      if (support) return support
    }
    return step
  }
  return null
}
