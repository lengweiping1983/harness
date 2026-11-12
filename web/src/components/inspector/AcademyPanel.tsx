import { useEffect, useRef, useState } from 'react'
import { BookOpen, Check, ChevronLeft, ChevronRight, Maximize2, Minimize2, RotateCcw, Send } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import type { AcademyCheckpoint, AcademyCourse, AcademyCourseSummary, AcademyFile, AcademyModelRun, AcademyProject, AcademyReceipt, AcademyRestorePreview, AcademyRun, AcademySourceFile, AcademySubmission, AcademyTutorProposal, AcademyTutorTurn, ResourceText } from '@/types'
import type {AcademyTutorCheckSelection} from '@/lib/protocol.generated'
import { academyApi, academyCourseProject, academyActiveLessonCompleted, academyLessonRun, academyProgressPercent, academyReceiptMatches, academyResumeLesson, isAcademyUnconfirmedWrite, watchAcademyModelRuns, watchAcademySubmissions, watchAcademyTutorTurns } from '@/lib/academy'
import type { ApiResult } from '@/lib/http'
import { noticeText } from '@/lib/noticeText'
import { Markdown } from '@/components/markdown/Markdown'
import { Badge, Button, EmptyHint, RefreshButton, Section, Spinner } from '@/components/ui/primitives'
import { cn } from '@/lib/utils'
import {createEditorFocusReturn} from '@/lib/editorFocusReturn'
import {AcademyAssessment, AcademyRubric} from './AcademyAssessment'
import {AcademyKnowledgeChecks} from './AcademyKnowledgeChecks'
import {AcademyLearningPath} from './AcademyLearningPath'
import {AcademyTopicStudio} from './AcademyTopicStudio'
import {AcademyModelLab} from './AcademyModelLab'
import {AcademyTutor} from './AcademyTutor'
import {AcademyRunOutput} from './AcademyRunOutput'
import {AcademyCodeEditor} from './AcademyCodeEditor'
import {AcademyRunComparison} from './AcademyRunComparison'
import {academyLineRange, type AcademySourceLocation} from '@/lib/academyDiagnostics'
import {useAppStore} from '@/store/app'

const modelFocusPath = 'model-prompt'
const viewKey = 'harness.academy.view.v1'
const draftKey = (project: string, path: string) => 'harness.academy.draft.v1:' + JSON.stringify([project, path])
const pendingKey = 'harness.academy.pending.v1'
const noteKey = (project: string, lesson: string) => 'harness.academy.note.v1:' + JSON.stringify([project, lesson])
type EditedSource = {path: string; content: string; expected_version: string}
type Pending = {modelStopInput?: {runId: string; requestId: string}; sourceInput?: EditedSource; tutorStopInput?: {turnId: string; requestId: string}; requestId: string; projectId?: string; lessonId?: string; courseId?: string; targetLessonId?: string;
  modelInput?: {prompt: string; fingerprint: string}; executionInput?: {kind: 'run' | 'check'; source?: EditedSource};
  tutorInput?: {prompt: string; allow_execution: boolean; require_execution: boolean; fingerprint: string; source?: EditedSource; knowledge_check?: AcademyTutorCheckSelection; model_run_id?: string; comparison_model_run_id?: string; evidence_run_id?: string; comparison_run_id?: string; proposal_path?: string}}
function validEditedSource(value: unknown, allowAbsent = false): value is EditedSource {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const source = value as EditedSource
  return typeof source.path === 'string' && typeof source.content === 'string' && typeof source.expected_version === 'string' && (/^[a-f0-9]{64}$/.test(source.expected_version) || allowAbsent && source.expected_version === 'absent')
}
function readPending(): Pending | null {
  const raw = sessionStorage.getItem(pendingKey)
  if (!raw) return null
  const value = JSON.parse(raw) as Pending
  if (typeof value.requestId !== 'string' || (value.projectId !== undefined && typeof value.projectId !== 'string') || (value.lessonId !== undefined && typeof value.lessonId !== 'string') || (value.courseId !== undefined && typeof value.courseId !== 'string')) throw Error('invalid pending operation')
  if (value.targetLessonId !== undefined && (typeof value.targetLessonId !== 'string' || !value.targetLessonId)) throw Error('invalid pending lesson')
  if (value.modelInput !== undefined && (!value.modelInput || typeof value.modelInput.prompt !== 'string' || !value.modelInput.prompt.trim() || [...value.modelInput.prompt].length > 8000 || typeof value.modelInput.fingerprint !== 'string' || !/^[a-f0-9]{64}$/.test(value.modelInput.fingerprint))) throw Error('invalid pending model input')
  if (value.executionInput !== undefined && (!value.executionInput || !['run', 'check'].includes(value.executionInput.kind)
    || (value.executionInput.source !== undefined && !validEditedSource(value.executionInput.source)))) throw Error('invalid pending execution input')
  if (value.sourceInput !== undefined && (!validEditedSource(value.sourceInput, true) || value.tutorStopInput || value.tutorInput || value.executionInput || value.modelInput || value.targetLessonId)) throw Error('invalid pending source write')
  if (value.modelStopInput !== undefined && (!value.modelStopInput || typeof value.modelStopInput.runId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value.modelStopInput.runId) || typeof value.modelStopInput.requestId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value.modelStopInput.requestId) || value.tutorStopInput || value.sourceInput || value.tutorInput || value.executionInput || value.modelInput || value.targetLessonId)) throw Error('invalid pending model stop')
  if (value.tutorStopInput !== undefined && (!value.tutorStopInput || typeof value.tutorStopInput.turnId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value.tutorStopInput.turnId) || typeof value.tutorStopInput.requestId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value.tutorStopInput.requestId) || value.tutorInput || value.executionInput || value.modelInput || value.targetLessonId)) throw Error('invalid pending tutor stop')
  if (value.tutorInput !== undefined && (!value.tutorInput || typeof value.tutorInput.prompt !== 'string' || !value.tutorInput.prompt.trim() || [...value.tutorInput.prompt].length > 4000
    || typeof value.tutorInput.allow_execution !== 'boolean' || typeof value.tutorInput.require_execution !== 'boolean' || typeof value.tutorInput.fingerprint !== 'string' || !/^[a-f0-9]{64}$/.test(value.tutorInput.fingerprint)
    || (value.tutorInput.knowledge_check !== undefined && (!value.tutorInput.knowledge_check || typeof value.tutorInput.knowledge_check.check_id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value.tutorInput.knowledge_check.check_id) || typeof value.tutorInput.knowledge_check.option_id !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(value.tutorInput.knowledge_check.option_id) || value.tutorInput.allow_execution || value.tutorInput.require_execution || value.tutorInput.source !== undefined || value.tutorInput.evidence_run_id !== undefined || value.tutorInput.model_run_id !== undefined || value.tutorInput.proposal_path !== undefined))
    || (value.tutorInput.comparison_model_run_id !== undefined && (typeof value.tutorInput.comparison_model_run_id !== 'string' || !value.tutorInput.comparison_model_run_id || value.tutorInput.comparison_model_run_id.length > 128 || !value.tutorInput.model_run_id || value.tutorInput.comparison_model_run_id === value.tutorInput.model_run_id))
    || (value.tutorInput.model_run_id !== undefined && (typeof value.tutorInput.model_run_id !== 'string' || !value.tutorInput.model_run_id || value.tutorInput.model_run_id.length > 128 || value.tutorInput.allow_execution || value.tutorInput.require_execution || value.tutorInput.source !== undefined || value.tutorInput.evidence_run_id !== undefined || value.tutorInput.proposal_path !== undefined))
    || (value.tutorInput.evidence_run_id !== undefined && (typeof value.tutorInput.evidence_run_id !== 'string' || !value.tutorInput.evidence_run_id || value.tutorInput.evidence_run_id.length > 128 || value.tutorInput.allow_execution || value.tutorInput.require_execution || value.tutorInput.source !== undefined))
    || (value.tutorInput.comparison_run_id !== undefined && (typeof value.tutorInput.comparison_run_id !== 'string' || !value.tutorInput.comparison_run_id || value.tutorInput.comparison_run_id.length > 128 || !value.tutorInput.evidence_run_id || value.tutorInput.comparison_run_id === value.tutorInput.evidence_run_id))
    || (value.tutorInput.proposal_path !== undefined && (typeof value.tutorInput.proposal_path !== 'string' || !value.tutorInput.proposal_path || value.tutorInput.proposal_path.length > 512 || value.tutorInput.allow_execution || value.tutorInput.require_execution || value.tutorInput.evidence_run_id !== undefined))
    || (value.tutorInput.source !== undefined && !validEditedSource(value.tutorInput.source)))) throw Error('invalid pending tutor input')
  return value
}

export function AcademyPanel({active = true}: {active?: boolean}) {
  const locale = useLocale()
  const editorRef = useRef<HTMLTextAreaElement | null>(null)
  const loadedSource = useRef<AcademySourceFile | null>(null)
  const modelPromptRef = useRef<HTMLTextAreaElement | null>(null)
  const loadedModelRuns = useRef<AcademyModelRun[] | null>(null)
  const modelFocusRequest = useRef<string | null>(null)
  const [modelFocus] = useState(createEditorFocusReturn)
  const [editorFocus] = useState(createEditorFocusReturn)
  const sourceFocus = useRef<(AcademySourceLocation & {projectId: string; lessonId: string; runId: string; epoch: number; origin: Element | null}) | null>(null)
  const expanded = useAppStore(state => state.inspectorExpanded)
  const practiceRef = useRef<HTMLElement | null>(null)
  const reflectionRef = useRef<HTMLElement | null>(null)
  const tutorRef = useRef<HTMLElement | null>(null)
  const tutorQuestionRef = useRef<HTMLTextAreaElement | null>(null)
  const guideRef = useRef<HTMLDetailsElement | null>(null)
  const [savedView] = useState(() => {
    try {
      const value: unknown = JSON.parse(localStorage.getItem(viewKey) ?? '{}')
      if (!value || typeof value !== 'object' || Array.isArray(value) || Object.values(value).some(item => typeof item !== 'string')) throw Error('invalid saved view')
      return value as Record<string, string>
    } catch {return {invalid: 'true'}}
  })
  const [courses, setCourses] = useState<AcademyCourseSummary[]>([])
  const [projects, setProjects] = useState<AcademyProject[]>([])
  const [courseId, setCourseId] = useState(savedView.courseId ?? '')
  const [projectId, setProjectId] = useState(savedView.projectId ?? '')
  const [course, setCourse] = useState<AcademyCourse | null>(null)
  const [project, setProject] = useState<AcademyProject | null>(null)
  const [lessonId, setLessonId] = useState(savedView.lessonId ?? '')
  const [files, setFiles] = useState<AcademyFile[]>([])
  const [path, setPath] = useState(savedView.path ?? '')
  const [source, setSource] = useState<AcademySourceFile | null>(null)
  const [draft, setDraft] = useState('')
  const [reference, setReference] = useState<AcademySourceFile | null>(null)
  const [runs, setRuns] = useState<AcademyRun[]>([])
  const [checkpoints, setCheckpoints] = useState<AcademyCheckpoint[]>([])
  const [preview, setPreview] = useState<AcademyRestorePreview | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [storageError, setStorageError] = useState(savedView.invalid === 'true')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [feedbackView, setFeedbackView] = useState({scope: '', open: false})
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState<ResourceText>('')
  const [name, setName] = useState('')
  const [newPath, setNewPath] = useState('')
  const [note, setNote] = useState('')
  const [submissions, setSubmissions] = useState<AcademySubmission[]>([])
  const [modelRuns, setModelRuns] = useState<AcademyModelRun[]>([])
  const [tutorTurns, setTutorTurns] = useState<AcademyTutorTurn[]>([])
  const [submissionId, setSubmissionId] = useState('')
  const [manageOpen, setManageOpen] = useState(!projectId)
  const [topicOpen, setTopicOpen] = useState(false)
  const [catalogOpen, setCatalogOpen] = useState(!!projectId)
  const [studyAnswers, setStudyAnswers] = useState<{scope: string; answers: Map<string, string>}>({scope: '', answers: new Map()})
  const studyVisit = useRef({identity: '', generation: 0})
  const refreshAfterWrite = useRef(false)
  const previewAbort = useRef<AbortController | null>(null)
  const referenceAbort = useRef<AbortController | null>(null)
  const submissionGeneration = useRef(0)
  const modelGeneration = useRef(0)
  const tutorGeneration = useRef(0)
  const settledTutors = useRef(new Set<string>())
  const projectRef = useRef(projectId)
  projectRef.current = projectId
  const composing = useRef(false)
  const writeLock = useRef(false)
  const generation = useRef(0)
  const activeRef = useRef(active)
  activeRef.current = active
  const dirty = source !== null && source.path === path && source.content !== draft
  const lesson = course?.lessons.find(item => item.id === lessonId)
  const modelLesson = lesson?.mode === 'model'
  const readingLesson = lesson?.mode === 'study'
  const study = readingLesson || modelLesson
  const modelCompleted = modelRuns.some(run => run.project_id === projectId && run.lesson_id === lessonId && run.status === 'completed' && run.input_fingerprint === project?.fingerprint)
  const reflectionReady = readingLesson || modelLesson && modelCompleted
  const studyIdentity = JSON.stringify([courseId, lessonId, projectId])
  if (studyVisit.current.identity !== studyIdentity) studyVisit.current = {identity: studyIdentity, generation: studyVisit.current.generation + 1}
  const studyScope = studyIdentity + ':' + studyVisit.current.generation
  const feedbackOpen = feedbackView.scope === studyScope && feedbackView.open
  const studyChecksReady = !!lesson?.knowledge_checks?.length && studyAnswers.scope === studyScope
    && lesson.knowledge_checks.every(check => studyAnswers.answers.get(check.id) === check.answer_id)
  const studyReady = study && !!note.trim() && studyChecksReady && (!modelLesson || modelCompleted)
  const studyCompleted = project?.progress.some(row => row.lesson_id === lessonId && row.state === 'completed') ?? false
  const studyStep = studyCompleted ? 'complete' : !studyChecksReady ? 'quiz' : !note.trim() ? 'notes' : 'complete'
  const lessonIndex = course?.lessons.findIndex(item => item.id === lessonId) ?? -1
  const lessonSubmissions = submissions.filter(item => item.project_id === projectId && item.lesson_id === lessonId)
  const selectedSubmission = lessonSubmissions.find(item => item.id === submissionId) ?? lessonSubmissions[0] ?? null
  const run = academyLessonRun(runs, projectId, lessonId)
  const runIsCurrent = !!run && !dirty && run.input_fingerprint === project?.fingerprint && !run.input_changed
  const current = project?.id === projectId && project.course_id === courseId && project.active_lesson_id === lessonId
  const locked = busy || pending !== null || storageError
  const activityCompleted = !dirty && !!course && !!project && current && academyActiveLessonCompleted(course, project, runs)
  const continueLesson = activityCompleted && course && project ? academyResumeLesson(course, project) : undefined
  const completedLessons = course && project ? course.lessons.filter(lesson => project.progress.some(row => row.lesson_id === lesson.id && row.state !== 'in_progress')).length : 0

  const hasProject = !!projectId
  useEffect(() => {setManageOpen(!hasProject); setCatalogOpen(hasProject)}, [hasProject])
  useEffect(() => {
    if (active && !busy && !pending && refreshAfterWrite.current) {
      refreshAfterWrite.current = false
      setRevision(value => value + 1)
    }
  }, [active, busy, pending])

  useEffect(() => {
    if (savedView.invalid === 'true') return
    try {localStorage.setItem(viewKey, JSON.stringify({courseId, projectId, lessonId, path}))}
    catch {setStorageError(true)}
  }, [courseId, projectId, lessonId, path])

  useEffect(() => {
    try {setPending(readPending())} catch {setStorageError(true)}
  }, [])

  useEffect(() => {
    composing.current = false
    return () => {composing.current = false}
  }, [active, projectId, lessonId])

  useEffect(() => {
    try {
      const saved = localStorage.getItem(noteKey(projectId, lessonId)) ?? ''
      if (saved.length > 4000) throw Error('invalid learning note')
      setNote(saved)
    } catch {setStorageError(true)}
  }, [projectId, lessonId])

  useEffect(() => {
    const epoch = ++submissionGeneration.current
    if (!active || !feedbackOpen || !projectId || busy || pending) return
    const stop = watchAcademySubmissions(projectId, {
      pending: submissions.some(item => item.project_id === projectId && item.status === 'evaluating'),
      publish: rows => {
        if (!activeRef.current || epoch !== submissionGeneration.current || projectRef.current !== projectId) return
        setSubmissions(rows)
      },
      failure: failure => {
        if (activeRef.current && epoch === submissionGeneration.current && projectRef.current === projectId) setError(failure.copy ?? failure.message)
      },
    })
    return () => {++submissionGeneration.current; stop()}
  }, [active, feedbackOpen, projectId, revision, busy, pending])

  useEffect(() => {
    const epoch = ++modelGeneration.current
    loadedModelRuns.current = null
    if (!active || !projectId || !modelLesson || busy || pending) return
    return watchAcademyModelRuns(projectId, {
      pending: modelRuns.some(run => run.project_id === projectId && run.status === 'running'),
      publish: rows => {
        if (activeRef.current && epoch === modelGeneration.current && projectRef.current === projectId) {loadedModelRuns.current = rows; setModelRuns(rows)}
      },
      failure: failure => {
        if (activeRef.current && epoch === modelGeneration.current && projectRef.current === projectId) setError(failure.copy ?? failure.message)
      },
    })
  }, [active, projectId, modelLesson, revision, busy, pending])

  useEffect(() => {
    const epoch = ++tutorGeneration.current
    if (!active || !projectId || busy || pending) return
    return watchAcademyTutorTurns(projectId, {
      pending: tutorTurns.some(turn => turn.project_id === projectId && turn.status === 'running'),
      publish: rows => {
        if (!activeRef.current || epoch !== tutorGeneration.current || projectRef.current !== projectId) return
        setTutorTurns(rows)
        let changed = false
        for (const row of rows) if (row.status !== 'running' && !settledTutors.current.has(row.id)) {
          settledTutors.current.add(row.id)
          changed = changed || row.actions.some(action => !!action.run_id)
        }
        if (changed) setRevision(value => value + 1)
      },
      failure: failure => {if (activeRef.current && epoch === tutorGeneration.current && projectRef.current === projectId) setError(failure.copy ?? failure.message)},
    })
  }, [active, projectId, revision, busy, pending])

  useEffect(() => {
    const controller = new AbortController()
    const epoch = ++generation.current
    if (!active) return () => controller.abort()
    setLoading(true)
    void (async () => {
      const [catalog, projectList, selectedCourse] = await Promise.all([
        academyApi.courses(controller.signal), academyApi.projects(controller.signal), courseId ? academyApi.course(courseId, controller.signal) : Promise.resolve(null),
      ])
      if (controller.signal.aborted || epoch !== generation.current) return
      if (!catalog.ok || !projectList.ok || (selectedCourse !== null && !selectedCourse.ok)) {
        const failure = [catalog, projectList, selectedCourse].find(result => result !== null && !result.ok)
        if (failure && !failure.ok) setError(failure.copy ?? failure.message)
        setLoading(false); return
      }
      setCourses(catalog.data.courses); setProjects(projectList.data.projects)
      if (!selectedCourse) {setCourseId(catalog.data.courses[0]?.id ?? ''); setLoading(false); return}
      if (!selectedCourse.ok) return
      setCourse(selectedCourse.data.course)
      if (!lessonId || !selectedCourse.data.course.lessons.some(item => item.id === lessonId)) setLessonId(selectedCourse.data.course.lessons[0]?.id ?? '')
      const selected = projectId ? await academyApi.project(projectId, controller.signal) : null
      if (controller.signal.aborted || epoch !== generation.current) return
      if (selected && !selected.ok) setError(selected.copy ?? selected.message)
      if (selected?.ok) {
        setProject(selected.data.project)
        const [fileList, checkpoints, history] = await Promise.all([
          academyApi.files(projectId, controller.signal), academyApi.checkpoints(projectId, controller.signal), academyApi.runs(projectId, controller.signal),
        ])
        if (controller.signal.aborted || epoch !== generation.current) return
        if (fileList.ok) {setFiles(fileList.data.files); if (!path || !fileList.data.files.some(file => file.path === path)) setPath(fileList.data.files[0]?.path ?? '')}
        if (checkpoints.ok) setCheckpoints(checkpoints.data.checkpoints)
        if (history.ok) setRuns(history.data.runs)
      } else if (!projectId) {setProject(null); setFiles([]); setRuns([]); setCheckpoints([])}
      setLoading(false)
    })()
    return () => {controller.abort(); ++generation.current}
  }, [active, locale, courseId, projectId, revision])

  useEffect(() => {
    loadedSource.current = null
    if (!active) return
    if (!projectId || !current || !path || study) {setSource(null); return}
    const controller = new AbortController()
    setSource(null)
    void academyApi.source(projectId, path, controller.signal).then(result => {
      if (controller.signal.aborted) return
      if (result.ok) {
        try {
          const stored = localStorage.getItem(draftKey(projectId, path))
          const draft = stored ? JSON.parse(stored) as {content: string; version: string} : null
          if (draft && (typeof draft.content !== 'string' || typeof draft.version !== 'string')) throw Error('invalid draft')
          const loaded = {...result.data, ...(draft ? {version: draft.version} : {})}
          loadedSource.current = loaded
          setSource(loaded)
          setDraft(draft?.content ?? result.data.content)
        } catch {setStorageError(true)}
      }
      else {sourceFocus.current = null; setSource(null); setError(result.copy ?? result.message)}
    })
    return () => controller.abort()
  }, [active, projectId, path, current, study, revision])

  useEffect(() => () => {
    previewAbort.current?.abort()
    referenceAbort.current?.abort()
  }, [active, projectId, lessonId])

  useEffect(() => {
    const target = sourceFocus.current
    if (!target) return
    if (!active || !current || dirty || locked || composing.current || target.projectId !== projectId || target.lessonId !== lessonId
      || target.runId !== run?.id || target.epoch !== generation.current || path !== target.path || !runIsCurrent
      || document.activeElement !== target.origin) {sourceFocus.current = null; return}
    if (!source || source.path !== target.path || !editorRef.current) return
    sourceFocus.current = null
    focusLine(target.line)
  }, [active, current, dirty, locked, projectId, lessonId, run, path, source, runIsCurrent])

  useEffect(() => () => {editorFocus.dispose(); modelFocus.dispose()}, [editorFocus, modelFocus])
  useEffect(() => {
    modelFocus.keepScope(active && current && modelLesson ? {projectId, lessonId, path: modelFocusPath} : null)
  }, [modelFocus, active, current, modelLesson, projectId, lessonId])
  useEffect(() => {
    if (!active || !current || !modelLesson || locked || loading || modelRuns !== loadedModelRuns.current || !modelPromptRef.current) return
    const observed = modelRuns.find(row => row.project_id === projectId && row.lesson_id === lessonId && row.request_id === modelFocusRequest.current)
    modelFocus.restore({projectId, lessonId, path: modelFocusPath}, observed?.id, modelPromptRef.current)
  }, [modelFocus, active, current, modelLesson, locked, loading, modelRuns, projectId, lessonId])
  useEffect(() => {
    editorFocus.keepScope(active && current ? {projectId, lessonId, path} : null)
  }, [editorFocus, active, current, projectId, lessonId, path])
  useEffect(() => {
    if (!active || !current || locked || loading || !source || source !== loadedSource.current || source.path !== path || !editorRef.current) return
    editorFocus.restore({projectId, lessonId, path}, run?.id, editorRef.current, source.version)
  }, [editorFocus, active, current, locked, loading, source, projectId, lessonId, path, run])

  const focusLine = (line: number) => {
    const editor = editorRef.current
    if (!editor || composing.current) return
    const range = academyLineRange(editor.value, line)
    if (!range) return
    editor.scrollIntoView({block: 'center'})
    editor.focus({preventScroll: true})
    editor.setSelectionRange(...range)
    const style = getComputedStyle(editor)
    const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.5
    editor.scrollTop = Math.max(0, (line - 1) * lineHeight - editor.clientHeight / 2)
  }
  const locateSource = (location: AcademySourceLocation) => {
    if (!runIsCurrent || !run || locked || loading || composing.current || !files.some(file => file.path === location.path)) return
    if (path === location.path && source?.path === path) {focusLine(location.line); return}
    sourceFocus.current = {...location, projectId, lessonId, runId: run.id, epoch: generation.current, origin: document.activeElement}
    setPath(location.path)
  }

  const clearPending = () => {
    try {sessionStorage.removeItem(pendingKey); setPending(null)}
    catch {setStorageError(true)}
  }
  const apply = (receipt: AcademyReceipt, identity: Pending) => {
    if (!academyReceiptMatches(receipt, identity.requestId, identity.projectId, identity.tutorStopInput?.requestId, identity.modelStopInput?.requestId)) {setError({key: 'ui.http.invalidResponse'}); return}
    if (identity.targetLessonId && receipt.status === 'ready' && receipt.result.project?.active_lesson_id !== identity.targetLessonId) {setError({key: 'ui.http.invalidResponse'}); return}
    if (identity.modelInput && receipt.status === 'ready' && (!receipt.result.model_run || receipt.result.model_run.prompt !== identity.modelInput.prompt || receipt.result.model_run.input_fingerprint !== identity.modelInput.fingerprint)) {setError({key: 'ui.http.invalidResponse'}); return}
    if (receipt.result.submission && identity.lessonId && receipt.result.submission.lesson_id !== identity.lessonId) {setError({key: 'ui.http.invalidResponse'}); return}
    if (receipt.result.model_run && identity.lessonId && receipt.result.model_run.lesson_id !== identity.lessonId) {setError({key: 'ui.http.invalidResponse'}); return}
    if (identity.sourceInput && receipt.status === 'ready' && (!receipt.result.source || receipt.result.source.path !== identity.sourceInput.path || receipt.result.source.content !== identity.sourceInput.content)) {setError({key: 'ui.http.invalidResponse'}); return}
    if (identity.modelStopInput && receipt.status === 'ready' && (!receipt.result.model_run || receipt.result.model_run.id !== identity.modelStopInput.runId || receipt.result.model_run.lesson_id !== identity.lessonId)) {setError({key: 'ui.http.invalidResponse'}); return}
    if (identity.tutorStopInput && receipt.status === 'ready' && (!receipt.result.tutor_turn || receipt.result.tutor_turn.id !== identity.tutorStopInput.turnId || receipt.result.tutor_turn.lesson_id !== identity.lessonId)) {setError({key: 'ui.http.invalidResponse'}); return}
    if (identity.tutorInput && receipt.status === 'ready') {
      const turn = receipt.result.tutor_turn
      const saved = identity.tutorInput.source
      if (!turn || turn.lesson_id !== identity.lessonId || turn.prompt !== identity.tutorInput.prompt || turn.allow_execution !== identity.tutorInput.allow_execution || turn.require_execution !== identity.tutorInput.require_execution
        || turn.knowledge_check?.check_id !== identity.tutorInput.knowledge_check?.check_id || turn.knowledge_check?.option_id !== identity.tutorInput.knowledge_check?.option_id
        || turn.comparison_model_run_id !== identity.tutorInput.comparison_model_run_id || turn.model_run_id !== identity.tutorInput.model_run_id || turn.evidence_run_id !== identity.tutorInput.evidence_run_id || turn.comparison_run_id !== identity.tutorInput.comparison_run_id || turn.proposal_path !== identity.tutorInput.proposal_path
        || (!saved && turn.input_fingerprint !== identity.tutorInput.fingerprint)
        || (saved && (!receipt.result.source || receipt.result.source.path !== saved.path || receipt.result.source.content !== saved.content))) {setError({key: 'ui.http.invalidResponse'}); return}
    }
    if (identity.executionInput && receipt.status === 'ready') {
      const run = receipt.result.run
      const saved = identity.executionInput.source
      if (!run || run.request_id !== identity.requestId || run.lesson_id !== identity.lessonId || run.kind !== identity.executionInput.kind
        || (saved && (!receipt.result.source || receipt.result.source.path !== saved.path || receipt.result.source.content !== saved.content))) {setError({key: 'ui.http.invalidResponse'}); return}
    }
    if (receipt.result.project && identity.courseId && receipt.result.project.course_id !== identity.courseId) {setError({key: 'ui.http.invalidResponse'}); return}
    if (receipt.status === 'unknown') {editorFocus.cancel(identity.requestId); modelFocus.cancel(identity.requestId); return}
    if (identity.modelInput) {
      if (receipt.status === 'ready' && receipt.result.model_run) modelFocus.settle(identity.requestId, receipt.result.model_run.id)
      else modelFocus.cancel(identity.requestId)
    }
    if (identity.executionInput) {
      if (receipt.status === 'ready' && receipt.result.run) editorFocus.settle(identity.requestId, receipt.result.run.id)
      else editorFocus.cancel(identity.requestId)
    }
    if (identity.sourceInput) {
      if (receipt.status === 'ready' && receipt.result.source) editorFocus.settle(identity.requestId, receipt.result.source.version)
      else editorFocus.cancel(identity.requestId)
    }
    if (receipt.status === 'failed') setError({key: 'ui.academy.error.' + receipt.result.error_code})
    else setError('')
    const next = receipt.result.project
    if (next) {setProjectId(next.id); setProject(next); setCourseId(next.course_id); setLessonId(next.active_lesson_id)}
    if (receipt.result.source) {
      const key = draftKey(identity.projectId ?? '', receipt.result.source.path)
      const saved = identity.sourceInput ?? identity.executionInput?.source ?? identity.tutorInput?.source
      const stored = localStorage.getItem(key)
      const unchanged = saved && stored === JSON.stringify({content: saved.content, version: saved.expected_version})
      if (unchanged) localStorage.removeItem(key)
      setSource(receipt.result.source); setDraft(receipt.result.source.content)
    }
    if (receipt.result.submission) {
      const submitted = receipt.result.submission
      setSubmissions(rows => [submitted, ...rows.filter(row => row.project_id === submitted.project_id && row.id !== submitted.id)])
      setSubmissionId(submitted.id)
    }
    if (receipt.result.model_run) {
      const run = receipt.result.model_run
      setModelRuns(rows => [run, ...rows.filter(row => row.project_id === run.project_id && row.id !== run.id)])
    }
    if (receipt.result.tutor_turn) {
      const turn = receipt.result.tutor_turn
      setTutorTurns(rows => [turn, ...rows.filter(row => row.project_id === turn.project_id && row.id !== turn.id)])
    }
    clearPending(); setPreview(null); setRevision(value => value + 1)
    return receipt.status === 'ready'
  }
  const perform = async (write: (requestId: string) => Promise<ApiResult<AcademyReceipt>>, submission = false, creatingCourseId?: string, details?: Pick<Pending, 'modelInput' | 'targetLessonId' | 'executionInput' | 'tutorInput' | 'tutorStopInput' | 'modelStopInput' | 'sourceInput'>, returnEditorFocus = false) => {
    if (locked || writeLock.current || composing.current) return
    const identity = {requestId: crypto.randomUUID(), courseId: creatingCourseId ?? courseId, ...(!creatingCourseId && projectId ? {projectId} : {}), ...(submission ? {lessonId} : {}), ...details}
    try {sessionStorage.setItem(pendingKey, JSON.stringify(identity))} catch {setStorageError(true); return}
    if (returnEditorFocus && details?.modelInput && modelPromptRef.current) {
      modelFocusRequest.current = identity.requestId
      modelFocus.begin(identity.requestId, {projectId, lessonId, path: modelFocusPath}, modelPromptRef.current)
    }
    if (returnEditorFocus && (details?.executionInput || details?.sourceInput) && editorRef.current) editorFocus.begin(identity.requestId, {projectId, lessonId, path}, editorRef.current, details.sourceInput ? 'save' : 'run')
    writeLock.current = true
    ++submissionGeneration.current
    ++modelGeneration.current
    ++tutorGeneration.current
    const epoch = generation.current
    setPending(identity); setBusy(true); setError('')
    const result = await write(identity.requestId)
    if (!result.ok || !activeRef.current || epoch !== generation.current) {editorFocus.cancel(identity.requestId); modelFocus.cancel(identity.requestId)}
    if (activeRef.current && epoch === generation.current) {
      if (result.ok) {if (!apply(result.data, identity)) modelFocus.cancel(identity.requestId)}
      else if (!isAcademyUnconfirmedWrite(result)) {clearPending(); setError(result.copy ?? result.message)}
      else setError(result.copy ?? result.message)
    }
    writeLock.current = false; setBusy(false)
  }
  const checkReceipt = async () => {
    if (!pending || busy) return
    setBusy(true)
    const epoch = generation.current
    const result = await academyApi.receipt(pending.requestId)
    if (activeRef.current && epoch === generation.current) {
      if (result.ok) apply(result.data, pending)
      else setError(result.copy ?? result.message)
    }
    setBusy(false)
  }
  const navigate = (action: () => void) => {
    if (dirty) {setError({key: 'ui.academy.unsaved'}); return}
    // 选课会使旧目录读取失效；原读取未结束时安排一次新读取，避免加载状态悬挂。
    if (loading) setRevision(value => value + 1)
    ++generation.current; setReference(null); setPreview(null); setError(''); action()
  }
  const showPreview = async (checkpointId: string) => {
    if (!project) return
    const epoch = generation.current
    previewAbort.current?.abort()
    const controller = new AbortController()
    previewAbort.current = controller
    const result = await academyApi.preview(project.id, checkpointId, controller.signal)
    if (controller.signal.aborted || previewAbort.current !== controller || !activeRef.current || epoch !== generation.current) return
    if (result.ok) setPreview(result.data)
    else setError(result.copy ?? result.message)
  }
  const showReference = async (referencePath: string) => {
    if (!lesson) return
    const epoch = generation.current
    referenceAbort.current?.abort()
    const controller = new AbortController()
    referenceAbort.current = controller
    const result = await academyApi.reference(courseId, lesson.id, referencePath, controller.signal)
    if (controller.signal.aborted || referenceAbort.current !== controller || !activeRef.current || epoch !== generation.current) return
    if (result.ok) setReference(result.data)
    else setError(result.copy ?? result.message)
  }
  const saveSource = (returnFocus = false) => {
    if (!project || !current || !source || source.path !== path) return
    const sourceInput = {path: source.path, content: draft, expected_version: source.version}
    void perform(requestId => academyApi.save(project.id, {request_id: requestId, ...sourceInput, expected_fingerprint: project.fingerprint}), false, undefined, {sourceInput}, returnFocus)
  }
  const addSource = () => {
    if (!project || !current || dirty || !newPath) return
    const sourceInput = {path: newPath, content: '', expected_version: 'absent'}
    void perform(requestId => academyApi.save(project.id, {request_id: requestId, ...sourceInput, expected_fingerprint: project.fingerprint}), false, undefined, {sourceInput})
  }
  const execute = (check: boolean, returnEditorFocus = false) => {
    if (!project || !current || !source || source.path !== path) return
    const edited = dirty ? {path: source.path, content: draft, expected_version: source.version} : undefined
    void perform(requestId => academyApi.run(project.id, {request_id: requestId, lesson_id: lessonId, expected_fingerprint: project.fingerprint, ...(edited ? {source: edited} : {})}, check), true, undefined,
      {executionInput: {kind: check ? 'check' : 'run', ...(edited ? {source: edited} : {})}}, returnEditorFocus)
  }
  const submit = () => {
    if (composing.current || !project || !current) return
    if (dirty || (study ? !note.trim() : !source || source.path !== path)) {setError({key: 'ui.academy.submitSaveFirst'}); return}
    void perform(requestId => academyApi.submit(project.id, {request_id: requestId, lesson_id: lessonId, expected_fingerprint: project.fingerprint, note}), true)
  }
  const askTutor = (prompt: string, allow_execution: boolean, require_execution: boolean, selection: {knowledge_check?: AcademyTutorCheckSelection; model_run_id?: string; comparison_model_run_id?: string; evidence_run_id?: string; comparison_run_id?: string; proposal_path?: string} = {}) => {
    if (!project || !current || composing.current || (dirty && (!source || source.path !== path))) return
    const edited = dirty && source && !selection.knowledge_check ? {path: source.path, content: draft, expected_version: source.version} : undefined
    if ((selection.evidence_run_id || selection.model_run_id) && (dirty || allow_execution || require_execution)) return
    const tutorInput = {prompt, allow_execution, require_execution, ...selection, fingerprint: project.fingerprint, ...(edited ? {source: edited} : {})}
    void perform(requestId => academyApi.tutor(project.id, {request_id: requestId, lesson_id: lessonId, expected_fingerprint: project.fingerprint, prompt, allow_execution, require_execution, ...selection, ...(edited ? {source: edited} : {})}), true, undefined, {tutorInput})
  }
  const tryProposal = (turn: AcademyTutorTurn, proposal: AcademyTutorProposal) => {
    if (!project || !current || locked || loading || composing.current) return
    if (dirty) {setError({key: 'ui.academy.unsaved'}); return}
    if (turn.project_id !== projectId || turn.lesson_id !== lessonId || turn.status !== 'completed' || turn.input_fingerprint !== project.fingerprint
      || proposal.path !== turn.proposal_path || !files.some(file => file.path === proposal.path && file.version === proposal.expected_version)) {
      setError({key: 'ui.academy.error.stale-project'}); return
    }
    try {
      const key = draftKey(projectId, proposal.path)
      if (localStorage.getItem(key) !== null) {setError({key: 'ui.academy.unsaved'}); return}
      localStorage.setItem(key, JSON.stringify({content: proposal.after, version: proposal.expected_version}))
      sourceFocus.current = null
      setError('')
      setPath(proposal.path)
      setSource({path: proposal.path, content: proposal.before, version: proposal.expected_version})
      setDraft(proposal.after)
      jump(practiceRef.current)
    } catch {setStorageError(true)}
  }
  const completeStudy = () => {
    if (composing.current || locked || loading || !project || !current || !studyReady || studyCompleted) return
    const answers = lesson!.knowledge_checks!.map(check => ({check_id: check.id, option_id: studyAnswers.answers.get(check.id)!}))
    void perform(requestId => academyApi.complete(project.id, {request_id: requestId, lesson_id: lessonId, expected_fingerprint: project.fingerprint, note, answers}))
  }
  const startCourse = (identity: string) => {
    if (dirty) {setError({key: 'ui.academy.unsaved'}); return}
    void perform(requestId => academyApi.create({course_id: identity, request_id: requestId}), false, identity)
  }
  const resumeProject = (saved: AcademyProject) => {
    if (locked || loading || composing.current) return
    navigate(() => {
      setCourseId(saved.course_id); setProjectId(saved.id); setLessonId(saved.active_lesson_id); setPath('')
      setSubmissions([]); setSubmissionId(''); setManageOpen(false)
    })
  }
  const openLesson = (targetLessonId: string) => {
    if (!project || locked || loading || dirty || composing.current) return
    referenceAbort.current?.abort(); previewAbort.current?.abort()
    setReference(null); setPreview(null)
    void perform(requestId => academyApi.start(project.id, targetLessonId, {
      request_id: requestId, expected_revision: project.revision, expected_fingerprint: project.fingerprint,
    }), false, undefined, {targetLessonId})
  }
  const jump = (target: HTMLElement | null) => {
    if (composing.current || !target) return
    if (target instanceof HTMLDetailsElement) target.open = true
    target.scrollIntoView({block: 'start'})
    target.focus({preventScroll: true})
  }
  const advanceStudy = () => {
    if (composing.current || locked || loading || !project || !current || studyCompleted || !reflectionReady) return
    if (studyStep === 'complete') {completeStudy(); return}
    const details = reflectionRef.current?.querySelector<HTMLDetailsElement>('[data-academy-section="' + studyStep + '"]')
    if (!details) return
    details.open = true
    if (studyStep === 'notes') {jump(details.querySelector('textarea')); return}
    const missing = lesson?.knowledge_checks?.find(check => studyAnswers.scope !== studyScope || studyAnswers.answers.get(check.id) !== check.answer_id)
    const form = [...details.querySelectorAll<HTMLFormElement>('[data-academy-check-id]')].find(item => item.dataset.academyCheckId === missing?.id)
    jump(form?.querySelector<HTMLInputElement>('input:checked') ?? form?.querySelector<HTMLInputElement>('input') ?? null)
  }

  const currentExercise = lesson?.exercise ? <div data-academy-task role="note" aria-label={t('ui.academy.experiment')} className="min-w-0 text-sm leading-reading"><Markdown workspaceLinks={false}>{lesson.exercise}</Markdown></div> : null

  const knowledgeChecks = lesson?.knowledge_checks?.length ? <details key={studyScope} data-academy-section="quiz" open={reflectionReady || !project || !current} className="border-t border-line pt-3"><summary className="cursor-pointer text-sm text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.quickCheck')}</summary><div className="mt-3"><AcademyKnowledgeChecks key={studyScope} embedded checks={lesson.knowledge_checks} disabled={locked || loading} asking={tutorTurns.some(turn => turn.project_id === projectId && turn.status === 'running')} onAsk={project && current ? (check_id, option_id) => {if (!composing.current) {askTutor(t('ui.academy.tutor.quizPrompt'), false, false, {knowledge_check: {check_id, option_id}}); jump(tutorRef.current)}} : undefined} onChecked={study && current ? (id, answer) => setStudyAnswers(previous => {
        const answers = new Map(previous.scope === studyScope ? previous.answers : [])
        answers.set(id, answer)
        return {scope: studyScope, answers}
      }) : undefined} /></div></details> : null

  if (!active) return null
  return <div className="refresh-action-container space-y-5" aria-label={t('ui.academy.title')}
    onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
    onKeyDown={event => {if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}>
    <div className="flex items-start justify-between gap-2"><div className="min-w-0"><h2 className="ui-section-title break-words">{project ? course?.title ?? t('ui.academy.title') : t('ui.academy.title')}</h2>{project && course && project.course_id === course.id ? <><p className="mt-1 text-xs text-muted">{t('ui.academy.progress', {done: completedLessons, total: course.lessons.length})}</p><progress className="sr-only" value={academyProgressPercent(course, project)} max={100} aria-label={t('ui.academy.progress', {done: completedLessons, total: course.lessons.length})} /></> : null}</div><div className="flex shrink-0 items-center gap-1">
      <Button size="xs" variant="ghost" icon={expanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />} aria-pressed={expanded}
        onClick={() => {if (!composing.current) useAppStore.getState().toggleInspectorExpanded()}}>{t(expanded ? 'ui.academy.exitFocus' : 'ui.academy.focus')}</Button>
      <RefreshButton size="xs" variant="ghost" label={t('ui.academy.refresh')} disabled={busy || dirty} onClick={() => setRevision(value => value + 1)} /></div></div>
    {loading ? <p role="status" className="flex gap-2 text-sm text-muted"><Spinner />{t('ui.academy.loading')}</p> : null}
    {storageError ? <p role="alert" className="text-sm text-danger">{t('ui.academy.error.store-invalid')}</p> : null}
    {pending ? <div role="status" className="space-y-2 rounded-xl border border-warn/40 p-3 text-sm"><p>{t('ui.academy.pending')}</p><p className="break-all font-mono text-xs">{t('ui.academy.receiptId', {id: pending.requestId})}</p><Button size="xs" disabled={busy} onClick={() => void checkReceipt()}>{t('ui.academy.recheckReceipt')}</Button></div> : null}
    {error ? <p role="alert" className="text-sm text-danger">{noticeText(error)}</p> : null}
    {dirty ? <div className="flex flex-wrap items-center gap-2 text-sm text-warn"><span>{t('ui.academy.draftRunHint')}</span><Button size="xs" variant="ghost" onClick={() => {localStorage.removeItem(draftKey(projectId, path)); setDraft(source?.content ?? ''); setRevision(value => value + 1)}}>{t('ui.academy.discard')}</Button></div> : null}
    <details data-academy-section="manage" open={manageOpen} onToggle={event => setManageOpen(event.currentTarget.open)} className="rounded-xl border border-line p-3">
      <summary className="cursor-pointer rounded-lg text-sm text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.manage')}</summary>
      <Section title={t('ui.academy.providedCourses')} className="mt-4">
        <ul className="space-y-3">{courses.filter(item => item.origin === 'provided').map(item => {const saved = academyCourseProject(item, projects, projectId); return <li key={item.id} data-course-id={item.id} className="space-y-2 rounded-xl border border-line p-3">
          <h3 className="text-sm font-medium">{item.title}</h3>
          {saved ? <p className="break-words text-xs text-muted">{t('ui.academy.resumeProjectName', {name: saved.name})}</p> : null}
          <p className="text-sm leading-reading text-muted">{item.description}</p>
          <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs text-muted">{t('ui.academy.lessonCount', {count: item.lesson_count})}</span>
            <Button size="sm" variant="subtle" disabled={locked || loading || dirty} aria-label={t(saved ? 'ui.academy.resumeCourse' : 'ui.academy.startCourse', {title: item.title})} icon={<BookOpen className="h-3.5 w-3.5" />} onClick={() => saved ? resumeProject(saved) : startCourse(item.id)}>{t(saved ? 'ui.academy.resumeCourseAction' : 'ui.academy.startDesignedCourse')}</Button></div>
        </li>})}</ul>
      </Section>
      <details data-academy-section="topic" open={topicOpen} onToggle={event => setTopicOpen(event.currentTarget.open)} className="mt-4 rounded-xl border border-line p-3">
      <summary className="cursor-pointer rounded-lg text-sm focus-visible:outline focus-visible:outline-brand">{t('ui.academy.createTopicCourse')}</summary>
      <AcademyTopicStudio active={active && manageOpen && topicOpen} disabled={locked} onCatalogChanged={() => {
        if (writeLock.current || pending) refreshAfterWrite.current = true
        else setRevision(value => value + 1)
      }} onOpenCourse={startCourse} />
      </details>
      <details data-academy-section="catalog" open={catalogOpen} onToggle={event => setCatalogOpen(event.currentTarget.open)} className="mt-4 rounded-xl border border-line p-3">
        <summary className="cursor-pointer rounded-lg text-sm text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.courseSettings')}</summary>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="min-w-0 space-y-1 text-sm">{t('ui.academy.chooseCourse')}<select className="ui-field w-full" value={courseId} disabled={locked} onChange={event => navigate(() => {setCourseId(event.target.value); setProjectId(''); setLessonId(''); setSubmissions([]); setSubmissionId('')})}>{(['provided', 'personal'] as const).map(origin => <optgroup key={origin} label={t(origin === 'provided' ? 'ui.academy.providedCourses' : 'ui.academy.personalCourses')}>{courses.filter(item => item.origin === origin).map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</optgroup>)}</select></label>
        <label className="space-y-1 text-sm">{t('ui.academy.projects')}<select className="ui-field w-full" value={projectId} disabled={locked} onChange={event => navigate(() => {const selected = projects.find(item => item.id === event.target.value); setProjectId(selected?.id ?? ''); setLessonId(selected?.active_lesson_id ?? ''); setSubmissions([]); setSubmissionId('')})}><option value="">{t('ui.academy.newProject')}</option>{projects.filter(item => item.course_id === courseId).map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      </div>

      {course ? <div className="mt-4 space-y-3">{course.assumptions ? <details className="rounded-xl border border-line p-3"><summary className="cursor-pointer rounded-lg text-sm text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.courseAssumptions')}</summary><div className="mt-3"><Markdown workspaceLinks={false}>{course.assumptions}</Markdown></div></details> : null}<AcademyLearningPath course={course} project={project} lessonId={lessonId} disabled={locked || loading} onSelect={id => navigate(() => setLessonId(id))} /><nav aria-label={t('ui.academy.lessons')} className="flex flex-wrap items-center gap-2">
        <Button size="xs" variant="ghost" disabled={locked || lessonIndex <= 0} aria-label={t('ui.academy.previousLesson')} icon={<ChevronLeft className="h-3.5 w-3.5" />} onClick={() => navigate(() => setLessonId(course.lessons[lessonIndex - 1].id))}>{t('ui.academy.previousLesson')}</Button>
        <label className="min-w-0 flex-1"><span className="sr-only">{t('ui.academy.lessons')}</span><select className="ui-field w-full" value={lessonId} disabled={locked} onChange={event => navigate(() => {setLessonId(event.target.value); setReference(null)})}>{course.lessons.map(item => <option key={item.id} value={item.id}>{t('ui.academy.lessonTitle', {order: item.order, title: item.title})}</option>)}</select></label>
        <Button size="xs" variant="ghost" disabled={locked || lessonIndex < 0 || lessonIndex >= course.lessons.length - 1} aria-label={t('ui.academy.nextLesson')} iconRight={<ChevronRight className="h-3.5 w-3.5" />} onClick={() => navigate(() => setLessonId(course.lessons[lessonIndex + 1].id))}>{t('ui.academy.nextLesson')}</Button>
      </nav></div> : null}
    {!project && course ? <Section title={t('ui.academy.createProject')}><p className="text-base leading-reading text-muted">{course.description}</p><div className="mt-3 flex flex-wrap gap-2"><input className="ui-field" aria-label={t('ui.academy.projectName')} value={name} maxLength={80} onChange={event => setName(event.target.value)} /><Button size="sm" disabled={locked} onClick={() => void perform(requestId => academyApi.create({course_id: courseId, request_id: requestId, ...(name.trim() ? {name: name.trim()} : {})}))}>{t('ui.academy.createProjectAction')}</Button></div></Section> : null}
      </details>
    </details>

    {!loading && !course && !error ? <EmptyHint icon={<BookOpen className="h-5 w-5" />} title={t('ui.academy.loadEmpty')} /> : null}
    {course && (project || catalogOpen) ? <>

      {project && project.course_version !== course.version ? <div role="status" className="space-y-2 rounded-xl border border-warn/40 p-3 text-sm">
        <p>{t('ui.academy.error.course-changed')}</p>
        <Button size="sm" disabled={locked || dirty} onClick={() => navigate(() => {setProjectId(''); setProject(null); setLessonId(course.lessons[0]?.id ?? ''); setPath(''); setSubmissions([]); setSubmissionId('')})}>{t('ui.academy.createProjectAction')}</Button>
      </div> : null}



      {lesson ? <Section>
        <h3 className="ui-section-title">{t('ui.academy.lessonTitle', {order: lesson.order, title: lesson.title})}</h3>

        <p className="text-base leading-reading">{lesson.goal}</p>

        <details key={JSON.stringify([projectId, lessonId])} ref={guideRef} open={readingLesson || !project || !current} tabIndex={-1} className="rounded-lg border border-line p-3 focus-visible:outline focus-visible:outline-brand">
          <summary className="cursor-pointer rounded-lg text-sm text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.lessonGuide')}</summary>
          <div className="mt-3 min-w-0 space-y-4">{lesson.prerequisites.length ? <div className="space-y-2 pt-2"><p className="text-xs text-muted">{t('ui.academy.prerequisiteHint')}</p><div className="flex flex-wrap gap-2">{lesson.prerequisites.map(id => {const prerequisite = course.lessons.find(item => item.id === id); return prerequisite ? <Button key={id} size="xs" variant="ghost" disabled={locked} onClick={() => navigate(() => setLessonId(id))}>{t('ui.academy.lessonTitle', {order: prerequisite.order, title: prerequisite.title})}</Button> : null})}</div></div> : null}<div className="flex flex-wrap gap-2 text-xs text-muted">{lesson?.stage ? <span>{lesson.stage}</span> : null}{lesson?.duration_minutes ? <span>{t('ui.academy.duration', {minutes: lesson.duration_minutes})}</span> : null}</div><Section title={t('ui.academy.concept')}><Markdown workspaceLinks={false}>{lesson.reading}</Markdown></Section>
          {lesson?.exercise && (readingLesson || !project || !current) ? <Section title={t('ui.academy.experiment')}><Markdown workspaceLinks={false}>{lesson.exercise}</Markdown></Section> : null}</div>
        </details>
        {project && !current ? <Button size="sm" disabled={locked || loading || dirty} onClick={() => openLesson(lesson.id)}>{t('ui.academy.openLesson')}</Button> : null}
      </Section> : null}
      <div className="academy-workbench"><div className="academy-workbench-grid">
      {!readingLesson || !project || !current ? <section ref={practiceRef} tabIndex={-1} aria-label={t('ui.academy.practiceWorkspace')} className="academy-workbench-main space-y-5 rounded-lg focus-visible:outline focus-visible:outline-brand">
      {!project || !current ? knowledgeChecks : null}
    {project && current ? <>

      {!study ? <Section title={t('ui.academy.source')}>
        {currentExercise}
        {files.length > 1 ? <label className="block text-sm">{t('ui.academy.chooseFile')}<select className="ui-field mt-1 w-full" value={path} disabled={locked || dirty} onChange={event => navigate(() => setPath(event.target.value))}>{files.map(file => <option value={file.path} key={file.path}>{file.path}</option>)}</select></label> : <p className="break-all font-mono text-xs text-muted">{path}</p>}
        {source && source.path === path ? <AcademyCodeEditor ref={editorRef} content={draft} dirty={dirty} disabled={locked} loading={loading} options={<details className="mt-3 text-xs text-muted"><summary className="cursor-pointer rounded-lg py-1 focus-visible:outline focus-visible:outline-brand">{t('ui.academy.executionLimits')}</summary><p className="mt-2 leading-reading">{t('ui.academy.limits')}</p></details>} onAsk={() => {if (!composing.current) {jump(tutorRef.current); tutorQuestionRef.current?.focus({preventScroll: true})}}} onChange={content => {
          setDraft(content)
          try {localStorage.setItem(draftKey(projectId, source.path), JSON.stringify({content, version: source.version}))}
          catch {setStorageError(true)}
        }} onSave={saveSource} onRun={execute} /> : null}
        {run ? <Section title={t('ui.academy.runResult')} className="mt-3"><p role="status" className={cn('text-sm', !runIsCurrent ? 'text-muted' : run.result.status === 'passed' ? 'text-ok' : 'text-danger')}>{!runIsCurrent ? t('ui.academy.previousRun') : t(run.kind === 'check' ? run.result.status === 'passed' && !run.input_changed ? 'ui.academy.checkPassed' : 'ui.academy.checkFailed' : run.result.status === 'passed' ? 'ui.academy.runPassed' : run.result.status === 'timeout' ? 'ui.academy.runTimeout' : 'ui.academy.runFailed')}</p><AcademyRunOutput run={run} root={project.root} files={files.map(file => file.path)} canLocate={runIsCurrent && !locked && !loading} onLocate={locateSource} /><AcademyRunComparison key={JSON.stringify([projectId, lessonId, run.id])} run={run} runs={runs} disabled={locked || loading || dirty || tutorTurns.some(turn => turn.project_id === projectId && turn.status === 'running')} onAsk={comparison => {if (!composing.current) {askTutor(t('ui.academy.compare.askPrompt'), false, false, {evidence_run_id: run.id, comparison_run_id: comparison.id}); jump(tutorRef.current)}}} />{run.input_changed ? <p className="text-sm text-warn">{t('ui.academy.inputChanged')}</p> : null}{run.result.truncated ? <p className="text-sm text-warn">{t('ui.academy.outputTruncated')}</p> : null}<div className="mt-3 space-y-2"><Button size="sm" variant="subtle" disabled={locked || dirty || tutorTurns.some(turn => turn.project_id === projectId && turn.status === 'running')} onClick={() => {if (!composing.current) {askTutor(t('ui.academy.tutor.resultPrompt'), false, false, {evidence_run_id: run.id}); jump(tutorRef.current)}}}>{t('ui.academy.tutor.explainResult')}</Button><p className="text-xs leading-reading text-muted">{t('ui.academy.tutor.resultHint')}</p></div><details data-academy-section="run-evidence" className="mt-3 text-xs text-muted"><summary className="cursor-pointer rounded-lg py-1 focus-visible:outline focus-visible:outline-brand">{t('ui.academy.runEvidence')}</summary><p className="mt-2 leading-reading">{t('ui.academy.fixtureHint')}</p><ol className="mt-2 space-y-1">{run.events.map(event => <li key={event.seq} className="text-sm"><Badge tone={event.source === 'host' ? 'brand' : 'neutral'}>{t('ui.academy.' + event.source)}</Badge> <span>{event.source === 'host' ? t('ui.academy.events.' + event.type) : lesson?.trace_labels[event.type] ?? event.type}</span></li>)}</ol></details></Section> : null}
        {project.sandbox === 'unavailable' ? <p className="mt-2 text-sm text-warn">{t('ui.academy.error.sandbox-unavailable')}</p> : null}

      </Section> : null}
      {modelLesson ? currentExercise : null}
      {modelLesson && lesson ? <AcademyModelLab key={JSON.stringify([projectId, lessonId])} projectId={projectId} lesson={lesson} runs={modelRuns} promptRef={modelPromptRef} disabled={locked || loading}
        onStop={run => {if (!composing.current && run.project_id === project.id && run.lesson_id === lessonId) void perform(requestId => academyApi.stopModel(project.id, run.id, {request_id: requestId}), true, undefined, {modelStopInput: {runId: run.id, requestId: run.request_id}})}}
        asking={tutorTurns.some(turn => turn.project_id === projectId && turn.status === 'running')}
        onAsk={(runId, comparisonId) => {if (!composing.current) {askTutor(t(comparisonId ? 'ui.academy.modelCompare.askPrompt' : 'ui.academy.tutor.modelResultPrompt'), false, false, {model_run_id: runId, ...(comparisonId ? {comparison_model_run_id: comparisonId} : {})}); jump(tutorRef.current)}}}
        onRefresh={() => setRevision(value => value + 1)} onRun={(prompt, returnFocus) => void perform(requestId => academyApi.modelRun(project.id, {request_id: requestId, lesson_id: lessonId, expected_fingerprint: project.fingerprint, prompt}), true, undefined, {modelInput: {prompt, fingerprint: project.fingerprint}}, returnFocus)} /> : null}
    </> : null}
      </section> : null}
      {project && current && lesson ? <aside ref={tutorRef} tabIndex={-1} aria-label={t('ui.academy.tutor.title')} className="academy-workbench-tutor rounded-lg focus-visible:outline focus-visible:outline-brand">
        <AcademyTutor key={JSON.stringify([projectId, lessonId])} projectId={projectId} lesson={lesson} fingerprint={project.fingerprint} dirty={dirty}
          turns={tutorTurns} onStop={turn => {if (!composing.current && project && turn.project_id === project.id && turn.lesson_id === lessonId) void perform(requestId => academyApi.stopTutor(project.id, turn.id, {request_id: requestId}), true, undefined, {tutorStopInput: {turnId: turn.id, requestId: turn.request_id}})}} questionRef={tutorQuestionRef} disabled={locked || loading} proposalPath={path || undefined} onSuggest={() => askTutor(t('ui.academy.tutor.proposalPrompt'), false, false, {proposal_path: path})} onTry={tryProposal} onAsk={askTutor} onRefresh={() => setRevision(value => value + 1)} />
      </aside> : null}
      {project && current ? <section ref={reflectionRef} aria-label={t('ui.academy.studyPractice')} className="academy-workbench-reflection space-y-5">
      {knowledgeChecks}
      <details key={'notes:' + studyScope} data-academy-section="notes" open={reflectionReady} className="border-t border-line pt-3"><summary className="cursor-pointer text-sm text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.notesDisclosure')}</summary><div className="mt-3"><Section><label className="block text-sm"><span className="sr-only">{t('ui.academy.note')}</span><textarea aria-label={t('ui.academy.note')} placeholder={t(readingLesson ? 'ui.academy.studyNoteHint' : modelLesson ? 'ui.academy.modelNoteHint' : 'ui.academy.noteHint')} value={note} maxLength={4000} disabled={locked} rows={4} className="ui-field w-full resize-y leading-reading" onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}} onChange={event => {setNote(event.target.value); try {localStorage.setItem(noteKey(projectId, lessonId), event.target.value)} catch {setStorageError(true)}}} /></label></Section></div></details>

      {reflectionReady ? <Section>
        {studyCompleted ? <p role="status" className="text-sm text-ok">{t('ui.academy.studyCompleted')}</p> : null}
        <Button data-academy-study-action data-study-step={studyStep} size="md" variant="primary" disabled={locked || loading || studyCompleted} icon={studyStep === 'complete' ? <Check className="h-3.5 w-3.5" /> : undefined} onClick={advanceStudy}>{t(studyStep === 'quiz' ? 'ui.academy.studyCheckNext' : studyStep === 'notes' ? 'ui.academy.studyNoteNext' : 'ui.academy.completeStudy')}</Button>
        <p className="mt-2 text-xs leading-reading text-muted">{t('ui.academy.studyCompletionHint')}</p>
      </Section> : null}
      {activityCompleted ? <Section title={t('ui.academy.continueTitle')}>
        {continueLesson ? <><Button size="md" variant="subtle" className="h-auto min-h-9 whitespace-normal text-left" disabled={locked || loading}
          iconRight={<ChevronRight className="h-3.5 w-3.5 shrink-0" />} onClick={() => openLesson(continueLesson.id)}>{t('ui.academy.continueAction', {order: continueLesson.order, title: continueLesson.title})}</Button>
          <p className="mt-2 text-xs leading-reading text-muted">{t('ui.academy.continueHint')}</p></> : <p role="status" className="text-sm leading-reading text-ok">{t('ui.academy.activitiesComplete')}</p>}
      </Section> : null}
      <details key={'feedback:' + studyScope} data-academy-section="feedback" open={feedbackOpen} onToggle={event => setFeedbackView({scope: studyScope, open: event.currentTarget.open})} className="border-t border-line pt-3"><summary className="cursor-pointer text-sm text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.feedbackDisclosure')}</summary><div className="mt-3 space-y-5">
      <form className="space-y-3" onSubmit={event => {event.preventDefault(); submit()}} onKeyDown={event => {if (event.key === 'Enter' && (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229)) event.preventDefault()}}>

        <AcademyRubric lesson={lesson} />
        <p className="text-xs leading-reading text-muted">{t(study ? 'ui.academy.studySubmissionHint' : 'ui.academy.submissionHint')}</p>
        {dirty ? <p className="text-sm text-warn">{t('ui.academy.submitSaveFirst')}</p> : null}
        <Button type="submit" size="md" variant={study ? 'secondary' : 'primary'} disabled={locked || dirty || (study ? !note.trim() : !source || source.path !== path)} icon={<Send className="h-3.5 w-3.5" />}>{t('ui.academy.submit')}</Button>
      </form>
      <Section title={t('ui.academy.feedback')}>
        {lessonSubmissions.length > 1 ? <label className="mb-3 block space-y-1 text-sm">{t('ui.academy.submissionHistory')}<select className="ui-field w-full" value={selectedSubmission?.id ?? ''} onChange={event => setSubmissionId(event.target.value)}>{lessonSubmissions.map((item, index) => <option key={item.id} value={item.id}>{t('ui.academy.submissionEntry', {number: lessonSubmissions.length - index})}</option>)}</select></label> : null}
        <AcademyAssessment submission={selectedSubmission} />
      </Section>
      </div></details>
      <details className="rounded-xl border border-line p-3"><summary className="cursor-pointer rounded-lg text-sm text-muted focus-visible:outline focus-visible:outline-brand">{t('ui.academy.extensions')}</summary><div className="mt-4 space-y-4">
        {lesson ? <Section title={t('ui.academy.readSource')}><div className="flex flex-wrap gap-2">{lesson.references.map(item => <Button size="xs" key={item.path} variant="ghost" onClick={() => void showReference(item.path)}>{item.path}</Button>)}</div>{reference ? <pre className="mt-2 max-h-64 overflow-auto whitespace-pre rounded-lg bg-canvas p-3 font-mono text-xs leading-code">{reference.content}</pre> : null}</Section> : null}
        {!study ? <Section title={t('ui.academy.newFiles')}><div className="flex flex-wrap gap-2"><input className="ui-field min-w-0" value={newPath} aria-label={t('ui.academy.newFile')} onChange={event => setNewPath(event.target.value)} /><Button size="xs" disabled={locked || dirty || !newPath} onClick={addSource}>{t('ui.academy.addFile')}</Button></div></Section> : null}
        <Section title={t('ui.academy.runHistory')}><ul className="space-y-1 text-xs">{runs.slice(0, 10).map(item => <li key={item.id} className="break-all font-mono">{t('ui.academy.historyEntry', {lesson: item.lesson_id, kind: t('ui.academy.kind.' + item.kind), status: t(item.result.status === 'passed' ? 'ui.academy.runPassed' : item.result.status === 'timeout' ? 'ui.academy.runTimeout' : 'ui.academy.runFailed'), fingerprint: item.input_fingerprint.slice(0, 12)})}</li>)}</ul></Section>
        <Section title={t('ui.academy.checkpoints')}><Button size="xs" variant="ghost" disabled={locked || dirty} onClick={() => void perform(requestId => academyApi.checkpoint(project.id, {request_id: requestId, expected_fingerprint: project.fingerprint}))}>{t('ui.academy.saveCheckpoint')}</Button><div className="mt-2 flex flex-wrap gap-2">{checkpoints.map(item => <Button key={item.id} size="xs" variant="ghost" disabled={locked || dirty} icon={<RotateCcw className="h-3.5 w-3.5" />} onClick={() => void showPreview(item.id)}>{item.lesson_id} · {t('ui.academy.checkpoint.' + item.kind)} · {item.id.slice(-6)}</Button>)}</div>{preview ? <div className="mt-3 space-y-2">{preview.changes.length ? preview.changes.map(change => <pre key={change.path} className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-canvas p-3 font-mono text-xs leading-code">{change.diff}</pre>) : <p className="text-sm text-muted">{t('ui.academy.noChanges')}</p>}<Button size="sm" disabled={locked || dirty} onClick={() => void perform(requestId => academyApi.restore(project.id, {request_id: requestId, checkpoint_id: preview.checkpoint_id, expected_fingerprint: preview.fingerprint}))}>{t('ui.academy.confirmRestore')}</Button><Button size="sm" variant="ghost" onClick={() => {previewAbort.current?.abort(); setPreview(null)}}>{t('ui.academy.cancel')}</Button></div> : null}</Section>
      </div></details>
      </section> : null}
      </div></div>
    </> : null}
  </div>
}
