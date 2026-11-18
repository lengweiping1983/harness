import {t} from '@/i18n'
import type {AcademyLesson, AcademyProject, AcademyTutorTurn} from '@/types'

export type AcademyStudioPhaseId = 'review' | 'struggle' | 'world' | 'steps' | 'reading' | 'teach' | 'checks' | 'note' | 'finish'
export interface AcademyStudioPhase {id: AcademyStudioPhaseId; optional: boolean}

/** 专注舞台环节编排：问题先行 → 动手世界 → 步步闯关 → 概念阅读 → 讲给 AI 听（可跳过）→ 快速测试 → 一句话反思 → 结课。 */
export function academyStudioPhases(lesson: Pick<AcademyLesson, 'struggle' | 'world' | 'steps' | 'knowledge_checks' | 'reading'>, dueReviews = false): AcademyStudioPhase[] {
  const phases: AcademyStudioPhase[] = []
  if (dueReviews) phases.push({id: 'review', optional: true})
  const practice = Boolean(lesson.struggle || lesson.world || lesson.steps?.length)
  if (lesson.struggle) phases.push({id: 'struggle', optional: false})
  if (lesson.world) phases.push({id: 'world', optional: false})
  if (lesson.steps?.length) phases.push({id: 'steps', optional: false})
  if (lesson.reading) phases.push({id: 'reading', optional: practice})
  if (lesson.knowledge_checks?.length) phases.push({id: 'teach', optional: true})
  if (lesson.knowledge_checks?.length) phases.push({id: 'checks', optional: false})
  phases.push({id: 'note', optional: false})
  phases.push({id: 'finish', optional: false})
  return phases
}

export interface AcademyStudioJourneyNode {id: string; order: number; title: string; state: 'completed' | 'active' | 'open'; mastery?: 'learning' | 'durable'; recommended?: boolean}

/** 学习旅程节点：已完成 / 当前 / 待解锁，供舞台顶部路径可视化。 */
export function academyStudioJourney(lessons: ReadonlyArray<Pick<AcademyLesson, 'id' | 'order' | 'title'>>, progress: AcademyProject['progress'], lessonId: string,
                                   skills: Readonly<Record<string, string>> = {}, recommendedId?: string): AcademyStudioJourneyNode[] {
  return lessons.map(lesson => {const mastery = skills[lesson.id]; return {id: lesson.id, order: lesson.order, title: lesson.title,
    state: lesson.id === lessonId ? 'active' : progress.some(row => row.lesson_id === lesson.id && row.state === 'completed') ? 'completed' : 'open',
    ...(mastery === 'learning' || mastery === 'durable' ? {mastery} : {}),
    ...(recommendedId === lesson.id && lesson.id !== lessonId ? {recommended: true} : {})}})
}

/** 完成当前环节后跳到下一个未完成环节；全部完成返回 null。 */
export function academyStudioNextPhase(phases: ReadonlyArray<AcademyStudioPhase>, done: ReadonlySet<string>, current: AcademyStudioPhaseId): AcademyStudioPhaseId | null {
  const index = phases.findIndex(phase => phase.id === current)
  for (let offset = index + 1; offset < phases.length; offset++) if (!done.has(phases[offset].id)) return phases[offset].id
  return null
}

/** 快速测试完成判定：每题都选中宿主正确答案。 */
export function academyStudioChecksDone(checks: ReadonlyArray<{id: string; answer_id: string}>, answers: ReadonlyMap<string, string>): boolean {
  return checks.length > 0 && checks.every(check => answers.get(check.id) === check.answer_id)
}

export type AcademyStudioCoachLevel = 0 | 1 | 2

/** 教练自主性档位对应的提示词资源键：自己先试 / 给提示 / 逐步讲解。 */
export function academyStudioCoachPromptKey(level: AcademyStudioCoachLevel): string {
  return level === 0 ? 'ui.academy.studio.coach.prompt.try' : level === 1 ? 'ui.academy.studio.coach.prompt.hint' : 'ui.academy.studio.coach.prompt.explain'
}

/** 步骤视图上报给舞台教练的当前步骤上下文。 */
export interface AcademyStepContext {stepId: string; prompt: string; answer?: string; correct?: boolean; confidence?: string}

/** 组装教练请求：档位指令 + 学习者问题（可为空）+ 课程标题与当前学习上下文，使苏格拉底引导指向学习者的实际作答。 */
export function academyStudioCoachPrompt(level: AcademyStudioCoachLevel, question: string, lessonTitle: string, context?: string): string {
  const base = t(academyStudioCoachPromptKey(level), {title: lessonTitle})
  const trimmed = question.trim()
  const withQuestion = trimmed ? base + '\n' + trimmed : base
  const trimmedContext = context?.trim()
  return trimmedContext ? withQuestion + '\n' + trimmedContext : withQuestion
}

/** 教练开场白记得学习者的状态：到期重测优先，其次未修复盲点，否则默认陪伴（确定性，不调用模型）。 */
export function academyStudioCoachOpening(mastery: {due_count: number; blindspots_open?: number; mistakes?: ReadonlyArray<{repaired?: boolean; lesson_title: string; at: number}>} | null): string {
  if (mastery && mastery.due_count > 0) return t('ui.academy.studio.coach.openingDue', {count: mastery.due_count})
  if (mastery && (mastery.blindspots_open ?? 0) > 0) return t('ui.academy.studio.coach.openingBlindspot', {count: mastery.blindspots_open})
  // 成长型思维：无待办时庆祝最近一次盲点修复（表扬进步而非能力），让教练的记得被感受到。
  const recentRepair = mastery?.mistakes?.filter(item => item.repaired).sort((a, b) => b.at - a.at)[0]
  if (recentRepair) return t('ui.academy.studio.coach.openingRepair', {title: recentRepair.lesson_title})
  return t('ui.academy.studio.coach.empty')
}

/** 舞台教练对话：排除讲给 AI 听（teach）回合，按时间排序。 */
export function academyStudioCoachTurns(turns: ReadonlyArray<AcademyTutorTurn>, projectId: string, lessonId: string): AcademyTutorTurn[] {
  return turns.filter(turn => turn.project_id === projectId && turn.lesson_id === lessonId && turn.mode !== 'teach').sort((a, b) => a.created_at - b.created_at)
}
