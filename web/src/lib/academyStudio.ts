import {t} from '@/i18n'
import type {AcademyLesson, AcademyProject, AcademyTutorTurn} from '@/types'

export type AcademyStudioPhaseId = 'review' | 'struggle' | 'world' | 'steps' | 'reading' | 'teach' | 'checks' | 'note' | 'finish'
export interface AcademyStudioPhase {id: AcademyStudioPhaseId; optional: boolean}

/** 专注舞台环节编排：问题先行 → 动手世界 → 步步闯关 → 概念阅读 → 教 AI 学生（可跳过）→ 快速测试 → 一句话反思 → 结课。 */
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

export interface AcademyStudioJourneyNode {id: string; order: number; title: string; state: 'completed' | 'active' | 'open'}

/** 学习旅程节点：已完成 / 当前 / 待解锁，供舞台顶部路径可视化。 */
export function academyStudioJourney(lessons: ReadonlyArray<Pick<AcademyLesson, 'id' | 'order' | 'title'>>, progress: AcademyProject['progress'], lessonId: string): AcademyStudioJourneyNode[] {
  return lessons.map(lesson => ({id: lesson.id, order: lesson.order, title: lesson.title,
    state: lesson.id === lessonId ? 'active' : progress.some(row => row.lesson_id === lesson.id && row.state === 'completed') ? 'completed' : 'open'}))
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

/** 组装教练请求：档位指令 + 学习者问题（可为空），附带课程标题上下文。 */
export function academyStudioCoachPrompt(level: AcademyStudioCoachLevel, question: string, lessonTitle: string): string {
  const base = t(academyStudioCoachPromptKey(level), {title: lessonTitle})
  const trimmed = question.trim()
  return trimmed ? base + '\n' + trimmed : base
}

/** 舞台教练对话：排除教 AI 学生（teach）回合，按时间排序。 */
export function academyStudioCoachTurns(turns: ReadonlyArray<AcademyTutorTurn>, projectId: string, lessonId: string): AcademyTutorTurn[] {
  return turns.filter(turn => turn.project_id === projectId && turn.lesson_id === lessonId && turn.mode !== 'teach').sort((a, b) => a.created_at - b.created_at)
}
