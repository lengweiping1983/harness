import type {AcademyLesson, AcademyProject, AcademySkillState} from '@/types'
import {academyStages} from './academy'

/** 技能地图节点：课程结构＋掌握度证据的确定性投影，坐标由阶段列/课次行编排。 */
export interface AcademySkillMapNode {
  id: string; title: string; order: number; x: number; y: number
  completed: boolean; active: boolean; recommended: boolean
  mastery?: 'learning' | 'durable'; retrievability?: number; due: boolean
}
export interface AcademySkillMapEdge {from: string; to: string; x1: number; y1: number; x2: number; y2: number}
export interface AcademySkillMapLayout {nodes: AcademySkillMapNode[]; edges: AcademySkillMapEdge[]; width: number; height: number; columns: number}

export const SKILL_MAP_COL = 190
export const SKILL_MAP_ROW = 104
/** 到期判定镜像宿主目标保持率（mastery.TARGET_RETENTION）；宿主队列仍是权威。 */
const DUE_RETENTION = 0.9

/** 先修关系图布局：相邻同阶段课次成一列，列内按课次顺序排行；先修边连接节点中心。 */
export function academySkillMap(lessons: ReadonlyArray<AcademyLesson>, progress: AcademyProject['progress'], skills: ReadonlyArray<AcademySkillState>,
                                activeLessonId?: string, recommendedId?: string): AcademySkillMapLayout {
  const stages = academyStages({lessons: [...lessons]})
  const skillById = new Map(skills.map(skill => [skill.lesson_id, skill]))
  const completedIds = new Set(progress.filter(row => row.state === 'completed').map(row => row.lesson_id))
  const nodes: AcademySkillMapNode[] = []
  const byId = new Map<string, AcademySkillMapNode>()
  stages.forEach((stage, column) => stage.lessons.forEach((lesson, row) => {
    const skill = skillById.get(lesson.id)
    const evidenced = Boolean(skill && skill.evidence_count > 0)
    const node: AcademySkillMapNode = {
      id: lesson.id, title: lesson.title, order: lesson.order,
      x: column * SKILL_MAP_COL + SKILL_MAP_COL / 2, y: row * SKILL_MAP_ROW + SKILL_MAP_ROW / 2,
      completed: completedIds.has(lesson.id), active: lesson.id === activeLessonId, recommended: lesson.id === recommendedId,
      ...(evidenced && skill && (skill.state === 'learning' || skill.state === 'durable') ? {mastery: skill.state} : {}),
      ...(evidenced && skill ? {retrievability: skill.retrievability} : {}),
      due: Boolean(evidenced && skill && skill.state !== 'new' && skill.retrievability < DUE_RETENTION),
    }
    nodes.push(node); byId.set(node.id, node)
  }))
  const edges: AcademySkillMapEdge[] = []
  for (const lesson of lessons) for (const prerequisite of lesson.prerequisites) {
    const from = byId.get(prerequisite), to = byId.get(lesson.id)
    if (from && to) edges.push({from: from.id, to: to.id, x1: from.x, y1: from.y, x2: to.x, y2: to.y})
  }
  const rows = Math.max(1, ...stages.map(stage => stage.lessons.length))
  return {nodes, edges, width: Math.max(1, stages.length) * SKILL_MAP_COL, height: rows * SKILL_MAP_ROW, columns: stages.length}
}

/** 先修边路径：跨列用水平贝塞尔，同列用侧弯，避免穿过节点标签。 */
export function academySkillMapEdgePath(edge: AcademySkillMapEdge): string {
  if (edge.x1 === edge.x2) return `M ${edge.x1} ${edge.y1} C ${edge.x1 + 70} ${edge.y1}, ${edge.x2 + 70} ${edge.y2}, ${edge.x2} ${edge.y2}`
  const dx = Math.max(30, (edge.x2 - edge.x1) / 2)
  return `M ${edge.x1} ${edge.y1} C ${edge.x1 + dx} ${edge.y1}, ${edge.x2 - dx} ${edge.y2}, ${edge.x2} ${edge.y2}`
}
