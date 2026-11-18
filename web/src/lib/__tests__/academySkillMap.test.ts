import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it} from 'vitest'
import {setLocale} from '@/i18n'
import {AcademySkillMap} from '@/components/inspector/AcademySkillMap'
import {academySkillMap, academySkillMapEdgePath, SKILL_MAP_COL, SKILL_MAP_ROW} from '../academySkillMap'
import type {AcademyLesson, AcademyProject, AcademySkillState} from '@/types'

afterEach(async () => {await setLocale('zh-CN')})

const lesson = (id: string, order: number, stage: string, prerequisites: string[] = []): AcademyLesson => ({
  id, order, mode: 'study', title: id, goal: id, reading: id, milestone: false, run_command: [], source_manifest: [],
  references: [], prerequisites, trace_labels: {}, stage, knowledge_checks: [],
})
const lessons = [lesson('fractions', 1, 'Stage A'), lesson('decimals', 2, 'Stage A'), lesson('ratios', 3, 'Stage B', ['fractions'])]
const skill = (patch: Partial<AcademySkillState> = {}): AcademySkillState => ({
  lesson_id: 'fractions', title: 'fractions', stage: 'Stage A', state: 'learning', stability_days: 2, difficulty: 5,
  retrievability: 0.62, due_at: 100, last_evidence_at: 1, evidence_count: 3, reviews: {attempts: 1, correct: 1, delayed_correct: 0, delayed_attempts: 0}, ...patch,
})
const progress: AcademyProject['progress'] = [{lesson_id: 'fractions', state: 'completed', checkpoint_id: '', evidence_ids: [], attempt_count: 1}]

it('lays out stages as columns and lessons as rows with prerequisite edges', () => {
  const layout = academySkillMap(lessons, progress, [skill()], 'decimals', 'ratios')
  expect(layout.columns).toBe(2)
  expect(layout.width).toBe(2 * SKILL_MAP_COL)
  expect(layout.height).toBe(2 * SKILL_MAP_ROW)
  const fractions = layout.nodes.find(node => node.id === 'fractions')!
  const decimals = layout.nodes.find(node => node.id === 'decimals')!
  const ratios = layout.nodes.find(node => node.id === 'ratios')!
  expect([fractions.x, fractions.y]).toEqual([SKILL_MAP_COL / 2, SKILL_MAP_ROW / 2])
  expect(decimals.y).toBe(SKILL_MAP_ROW + SKILL_MAP_ROW / 2)
  expect(ratios.x).toBe(SKILL_MAP_COL + SKILL_MAP_COL / 2)
  expect(layout.edges).toEqual([{from: 'fractions', to: 'ratios', x1: fractions.x, y1: fractions.y, x2: ratios.x, y2: ratios.y}])
  expect(fractions.completed).toBe(true)
  expect(decimals.active).toBe(true)
  expect(ratios.recommended).toBe(true)
  expect(fractions.mastery).toBe('learning')
  expect(fractions.retrievability).toBe(0.62)
  expect(fractions.due).toBe(true)
})

it('projects only evidenced skills into halos and ignores missing prerequisites', () => {
  const layout = academySkillMap(lessons, [], [skill({lesson_id: 'decimals', evidence_count: 0, state: 'new'}), skill({lesson_id: 'ratios', state: 'durable', retrievability: 0.95})], undefined, undefined)
  const fractions = layout.nodes.find(node => node.id === 'fractions')!
  const decimals = layout.nodes.find(node => node.id === 'decimals')!
  const ratios = layout.nodes.find(node => node.id === 'ratios')!
  expect(fractions.retrievability).toBeUndefined()
  expect(fractions.mastery).toBeUndefined()
  expect(decimals.retrievability).toBeUndefined()
  expect(ratios.mastery).toBe('durable')
  expect(ratios.due).toBe(false)
  const orphan = academySkillMap([lesson('solo', 1, 'Stage A', ['missing'])], [], [])
  expect(orphan.edges).toEqual([])
})

it('draws cross-column and same-column prerequisite curves', () => {
  const cross = academySkillMapEdgePath({from: 'a', to: 'b', x1: 95, y1: 52, x2: 285, y2: 52})
  expect(cross).toContain('C')
  expect(cross.startsWith('M 95 52')).toBe(true)
  const same = academySkillMapEdgePath({from: 'a', to: 'b', x1: 95, y1: 52, x2: 95, y2: 156})
  expect(same).toContain('165')
})

it('renders the skill map with halos, legend and keyboard-openable nodes in both languages', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(createElement(AcademySkillMap, {lessons, progress, skills: [skill()], activeLessonId: 'decimals', recommendedId: 'ratios', onOpenLesson: () => {}}))
  expect(html).toContain('data-academy-skill-map')
  expect(html).toContain('data-skill-map-node="fractions"')
  expect(html).toContain('data-skill-map-edge')
  expect(html).toContain('data-skill-map-halo')
  expect(html).toContain('Memory retention 62%')
  expect(html).toContain('role="button"')
  expect(html).toContain('tabindex="0"')
  // 漫游 tabindex：仅首节点可 Tab 进入，其余 -1；键盘提示存在。
  expect(html).toContain('data-skill-map-node="fractions" transform="translate(95,52)" role="button" tabindex="0"')
  expect(html).toContain('data-skill-map-node="decimals" transform="translate(95,156)" role="button" tabindex="-1"')
  expect(html).toContain('Arrow keys move between skills')
  expect(html).toContain('aria-current="step"')
  expect(html).toContain('Skill map')
  expect(html).toContain('Completed')
  expect(html).toContain('Due')
  const readOnly = renderToStaticMarkup(createElement(AcademySkillMap, {lessons, progress, skills: []}))
  expect(readOnly).toContain('role="img"')
  expect(readOnly).not.toContain('data-skill-map-halo')
  expect(readOnly).not.toContain('Arrow keys move between skills')
  const empty = renderToStaticMarkup(createElement(AcademySkillMap, {lessons: [], progress: [], skills: []}))
  expect(empty).toContain('The skill map appears once the course structure loads.')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(createElement(AcademySkillMap, {lessons, progress, skills: [skill()], onOpenLesson: () => {}}))
  expect(chinese).toContain('技能地图')
  expect(chinese).toContain('记忆保持 62%')
  expect(chinese).toContain('待重测')
  expect(chinese).toContain('长期掌握')
})
