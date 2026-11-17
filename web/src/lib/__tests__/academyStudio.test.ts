import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, expect, it} from 'vitest'
import {setLocale} from '@/i18n'
import {AcademyStudio} from '@/components/inspector/AcademyStudio'
import {academyStudioChecksDone, academyStudioCoachPrompt, academyStudioCoachTurns, academyStudioJourney, academyStudioNextPhase, academyStudioPhases} from '../academyStudio'
import type {AcademyCourse, AcademyLesson, AcademyProject, AcademyStep, AcademyTutorTurn} from '@/types'

afterEach(async () => {await setLocale('zh-CN')})

const step = (id: string): AcademyStep => ({id, kind: 'numeric', prompt: 'How many?', feedback: 'Think about equal parts.', tolerance: 0})
const check = {id: 'c1', prompt: 'Which is half?', options: [{id: 'a', label: 'One of two equal parts', feedback: 'Equal parts share one name.'}, {id: 'b', label: 'Any two pieces', feedback: 'Pieces must be equal.'}], answer_id: 'a', hints: ['Equal parts matter.']}
const lesson = (patch: Partial<AcademyLesson> = {}): AcademyLesson => ({id: 'fractions', mode: 'study', order: 2, title: 'Fractions', goal: 'Understand halves',
  reading: 'A half is one of two equal parts.', milestone: false, run_command: [], source_manifest: [], references: [], prerequisites: [], trace_labels: {},
  knowledge_checks: [check], steps: [step('s1'), step('s2')], struggle: {challenge: 'Show half without numbers.', window_minutes: 3}, world: {kind: 'fraction-bar'}, ...patch})
const nextLesson: AcademyLesson = {...lesson({id: 'decimals', order: 3, title: 'Decimals', struggle: undefined, world: undefined, steps: undefined})}
const course: AcademyCourse = {id: 'math', version: '1', origin: 'provided', source_revision: 'fixture', title: 'Math foundations', description: '', references: [],
  lessons: [lesson({id: 'place-value', order: 1, title: 'Place value', struggle: undefined, world: undefined, steps: undefined}), lesson(), nextLesson]}
const project: AcademyProject = {id: 'project', course_id: 'math', course_version: '1', name: 'Learner', revision: 1, active_lesson_id: 'fractions',
  course_fingerprint: 'a'.repeat(64), created_at: 1, progress: [{lesson_id: 'place-value', state: 'completed', checkpoint_id: '', evidence_ids: [], attempt_count: 1}],
  root: '/fixture', fingerprint: 'a'.repeat(64), sandbox: 'unavailable'}
const studio = (overrides: Partial<Parameters<typeof AcademyStudio>[0]> = {}) => createElement(AcademyStudio, {
  course, project, lesson: lesson(), active: false, disabled: false, tutorTurns: [], struggleRevealed: false,
  checkAnswers: new Map(), note: '', studyReady: false, studyCompleted: false,
  onClose: () => {}, onOpenLesson: () => {}, onAskCoach: () => {}, onStrugglePhase: () => {}, onCheckAnswer: () => {}, onNote: () => {}, onComplete: () => {}, onRefresh: () => {},
  ...overrides,
})

it('orders phases problem-first and marks teach and practice-backed reading optional', () => {
  expect(academyStudioPhases(lesson()).map(phase => phase.id)).toEqual(['struggle', 'world', 'steps', 'reading', 'teach', 'checks', 'note', 'finish'])
  const phases = academyStudioPhases(lesson())
  expect(phases.find(phase => phase.id === 'teach')?.optional).toBe(true)
  expect(phases.find(phase => phase.id === 'reading')?.optional).toBe(true)
  const due = academyStudioPhases(lesson(), true)
  expect(due.map(phase => phase.id)).toEqual(['review', 'struggle', 'world', 'steps', 'reading', 'teach', 'checks', 'note', 'finish'])
  expect(due[0].optional).toBe(true)
  const readingOnly = academyStudioPhases(lesson({struggle: undefined, world: undefined, steps: undefined}))
  expect(readingOnly.map(phase => phase.id)).toEqual(['reading', 'teach', 'checks', 'note', 'finish'])
  expect(readingOnly.find(phase => phase.id === 'reading')?.optional).toBe(false)
})

it('builds journey states and advances to the next unfinished phase only', () => {
  const journey = academyStudioJourney(course.lessons, project.progress, 'fractions')
  expect(journey.map(node => node.state)).toEqual(['completed', 'active', 'open'])
  const mastered = academyStudioJourney(course.lessons, project.progress, 'fractions', {fractions: 'durable', 'place-value': 'learning', decimals: 'new'})
  expect(mastered.map(node => node.mastery)).toEqual(['learning', 'durable', undefined])
  const phases = academyStudioPhases(lesson())
  expect(academyStudioNextPhase(phases, new Set(['struggle']), 'struggle')).toBe('world')
  expect(academyStudioNextPhase(phases, new Set(['world', 'steps', 'reading', 'teach', 'checks', 'note']), 'note')).toBe('finish')
  expect(academyStudioNextPhase(phases, new Set(['finish']), 'finish')).toBeNull()
})

it('requires every check answered correctly before checks count as done', () => {
  expect(academyStudioChecksDone([check], new Map([['c1', 'b']]))).toBe(false)
  expect(academyStudioChecksDone([check], new Map([['c1', 'a']]))).toBe(true)
  expect(academyStudioChecksDone([], new Map())).toBe(false)
})

it('builds coach prompts per autonomy level in both languages and appends the learner question', async () => {
  await setLocale('en')
  const tryPrompt = academyStudioCoachPrompt(0, '  Why equal parts?  ', 'Fractions')
  expect(tryPrompt).toContain('Fractions')
  expect(tryPrompt).toContain('one short question')
  expect(tryPrompt.endsWith('Why equal parts?')).toBe(true)
  expect(academyStudioCoachPrompt(1, '', 'Fractions')).toContain('one hint')
  expect(academyStudioCoachPrompt(2, '', 'Fractions')).toContain('step by step')
  await setLocale('zh-CN')
  expect(academyStudioCoachPrompt(0, '', '分数')).toContain('一个简短问题')
  expect(academyStudioCoachPrompt(1, '', '分数')).toContain('不要直接给答案')
  expect(academyStudioCoachPrompt(2, '', '分数')).toContain('一步步讲解')
})

it('keeps coach turns separate from teach turns and sorted by time', () => {
  const turn = (patch: Partial<AcademyTutorTurn>): AcademyTutorTurn => ({id: 't', project_id: 'project', lesson_id: 'fractions', request_id: 'r',
    input_fingerprint: 'a'.repeat(64), prompt: 'p', allow_execution: false, require_execution: false, response: 'ok', model: 'm', language: 'en',
    status: 'completed', actions: [], created_at: 1, ...patch})
  const rows = [turn({id: 'late', created_at: 5}), turn({id: 'teach', mode: 'teach', created_at: 2}),
    turn({id: 'other-lesson', lesson_id: 'decimals', created_at: 3}), turn({id: 'other-project', project_id: 'other', created_at: 4}), turn({id: 'early', created_at: 1})]
  expect(academyStudioCoachTurns(rows, 'project', 'fractions').map(item => item.id)).toEqual(['early', 'late'])
})

it('renders the immersive studio with journey, single focus phase and coach strip in both languages', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(studio())
  expect(html).toContain('data-academy-studio')
  expect(html).toContain('role="dialog"')
  expect(html).toContain('Focus learning studio')
  expect(html).toContain('data-studio-journey')
  expect(html).toContain('Place value')
  expect(html).toContain('aria-current="step"')
  expect(html).toContain('data-studio-phase="struggle"')
  expect(html).toContain('Try first')
  expect(html).toContain('data-studio-coach')
  expect(html).toContain('data-coach-level="0"')
  expect(html).toContain('data-coach-level="2"')
  expect(html).toContain('Coach involvement')
  expect(html).toContain('The coach is nearby')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(studio())
  expect(chinese).toContain('专注学习舞台')
  expect(chinese).toContain('学习旅程')
  expect(chinese).toContain('教练参与度')
  expect(chinese).toContain('自己先试')
})

const coachTurn = (patch: Partial<AcademyTutorTurn> = {}): AcademyTutorTurn => ({id: 'turn', project_id: 'project', lesson_id: 'fractions', request_id: 'r',
  input_fingerprint: 'a'.repeat(64), prompt: 'base instruction\nWhy equal parts?', allow_execution: false, require_execution: false, response: 'Because halves share one name.',
  model: 'm', language: 'en', status: 'completed', actions: [], created_at: 1, ...patch})

it('shows a focus timer and a multi-turn coach thread that excludes teach turns', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(studio({tutorTurns: [coachTurn(), coachTurn({id: 'teach', mode: 'teach', created_at: 2}), coachTurn({id: 'late', created_at: 3, prompt: 'no newline prompt'})]}))
  expect(html).toContain('data-studio-elapsed')
  expect(html).toContain('0 min focused')
  expect(html).toContain('data-studio-coach-thread')
  expect(html).toContain('Why equal parts?')
  expect(html).toContain('Help me with this phase')
  expect(html).toContain('Because halves share one name.')
  expect(html).not.toContain('base instruction')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(studio({tutorTurns: [coachTurn({prompt: 'plain ask'})]}))
  expect(chinese).toContain('已专注 0 分钟')
  expect(chinese).toContain('帮我理解当前环节')
})
it('shows a session plan with estimate and phase monitoring in both languages', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(studio())
  expect(html).toContain('data-studio-session')
  expect(html).toContain('Today&#x27;s session')
  expect(html).toContain('New: Fractions')
  expect(html).toContain('Reflect &amp; finish')
  expect(html).toContain('~15 min')
  expect(html).toContain('0/8 phases')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(studio())
  expect(chinese).toContain('今日会话')
  expect(chinese).toContain('新课：Fractions')
  expect(chinese).toContain('反思与结课')
})

it('offers a dismissible proactive coach nudge after the struggle reveal without calling the model', async () => {
  await setLocale('en')
  const html = renderToStaticMarkup(studio({struggleRevealed: true}))
  expect(html).toContain('data-studio-nudge="struggle"')
  expect(html).toContain('Your struggle just now was valuable')
  expect(html).toContain('Get one hint')
  const plain = renderToStaticMarkup(studio())
  expect(plain).not.toContain('data-studio-nudge')
  await setLocale('zh-CN')
  const chinese = renderToStaticMarkup(studio({struggleRevealed: true}))
  expect(chinese).toContain('刚才的挣扎很有价值')
})

it('shows the finish checklist from existing evidence and celebrates completion with the next lesson', async () => {
  await setLocale('en')
  const checklist = renderToStaticMarkup(studio({checkAnswers: new Map([['c1', 'a']]), note: 'I learned halves.', studyReady: true}))
  expect(checklist).toContain('data-studio-phase="finish"')
  expect(checklist).toContain('Quick check passed')
  expect(checklist).toContain('Reflection written')
  expect(checklist).toContain('Complete this lesson activity')
  const done = renderToStaticMarkup(studio({studyCompleted: true, studyReady: true, checkAnswers: new Map([['c1', 'a']]), note: 'I learned halves.'}))
  expect(done).toContain('Great work')
  expect(done).toContain('Next lesson: Decimals')
  expect(done).toContain('Back to panel to review')
})
