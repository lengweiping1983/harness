import { useCallback, useId, useMemo, useRef, useState, type KeyboardEvent, type MutableRefObject, type RefObject } from 'react'
import { useLocale } from '@/i18n'
import { useAppStore } from '@/store/app'
import { api } from '@/lib/api'
import { showLatestChanges } from '@/store/changeCards'
import { applyCompletion, detectTrigger, rankCandidates, groupedCandidates, REFERENCE_PAGE_SIZE, type CandidateKind, type ScoredCandidate, type TriggerState } from '@/lib/trigger'
import { commandCandidates, skillCandidates, type CommandId, type CommandSpec } from '@/lib/triggerSources'
import { candidateKey, selectedCandidateIndex } from '@/lib/fileReferences'
import { useFileReferences } from './useFileReferences'
import type { ComposerMode } from '@/lib/composerMode'
import type { PendingCompletion } from './useComposerEditor'

interface CompletionOptions {
  draftKey: string
  sessionId: string | null
  taRef: RefObject<HTMLTextAreaElement>
  valueRef: MutableRefObject<string>
  pendingCompletion: MutableRefObject<PendingCompletion | null>
  setValue: (text: string) => void
  resize: () => void
  scheduleFrame: (callback: () => void) => void
  selectMode: (mode: ComposerMode, focus?: boolean) => void
}

/** Keep candidate state, file lookup and slash-command dispatch scoped to the draft. */
export function useComposerCompletion({draftKey, sessionId, taRef, valueRef, pendingCompletion, setValue, resize, scheduleFrame, selectMode}: CompletionOptions) {
  const locale = useLocale()
  const sessions = useAppStore(s => s.sessions)
  const skills = useAppStore(s => s.skills)
  const startNewSession = useAppStore(s => s.startNewSession)
  const toggleTheme = useAppStore(s => s.toggleTheme)
  const setInspectorTab = useAppStore(s => s.setInspectorTab)
  const toggleSidebar = useAppStore(s => s.toggleSidebar)
  const setShortcutsOpen = useAppStore(s => s.setShortcutsOpen)
  const setModelMenuOpen = useAppStore(s => s.setModelMenuOpen)

  const [rawTrigger, setRawTrigger] = useState<(TriggerState & { owner: string }) | null>(null)
  const trigger = rawTrigger?.owner === draftKey ? rawTrigger : null
  const [selection, setSelection] = useState<{ query: string; candidate: string } | null>(null)
  const [referencePages, setReferencePages] = useState<{ key: string; limits: Partial<Record<CandidateKind, number>> }>({ key: '', limits: {} })
  const lastTriggerKey = useRef<string | null>(null)
  const setTrigger = useCallback((next: (TriggerState & { owner: string }) | null) => {
    const key = next ? JSON.stringify([next.owner, next.kind, next.query, next.from]) : null
    if (!next || key !== lastTriggerKey.current) {
      setSelection(null)
      setReferencePages({ key: '', limits: {} })
    }
    lastTriggerKey.current = key
    setRawTrigger(next)
  }, [])
  const triggerMenuId = useId()
  const closeTrigger = useCallback(() => setTrigger(null), [])
  const fileRoot = sessions.find((session) => session.id === sessionId)?.resolved_cwd
  const files = useFileReferences(sessionId, trigger?.kind === 'reference' ? trigger.query : null, fileRoot)
  const selectionQuery = JSON.stringify([draftKey, trigger?.kind, trigger?.query, trigger?.from])

  const allItems = useMemo(() => {
    if (!trigger) return []
    const pool =
      trigger.kind === 'reference'
        ? [...files.candidates, ...skillCandidates(skills)]
        : commandCandidates()
    return rankCandidates(pool, trigger.query, trigger.kind === 'reference' ? Infinity : 40)
  }, [trigger, files.candidates, sessions, skills, locale])
  const referenceLimits = referencePages.key === selectionQuery ? referencePages.limits : {}
  const items = trigger?.kind === 'reference' ? groupedCandidates(allItems, referenceLimits) : allItems
  const showMoreReferences = (kind: CandidateKind) => setReferencePages({
    key: selectionQuery,
    limits: { ...referenceLimits, [kind]: (referenceLimits[kind] ?? REFERENCE_PAGE_SIZE) + REFERENCE_PAGE_SIZE },
  })
  const activeIndex = selectedCandidateIndex(items, selection?.query === selectionQuery ? selection.candidate : null)

  const syncTrigger = useCallback(() => {
    const ta = taRef.current
    if (!ta) return
    const next = detectTrigger(ta.value, ta.selectionStart ?? ta.value.length)
    setTrigger(next ? { ...next, owner: draftKey } : null)
  }, [draftKey])

  /**
   * 命令派发。
   *
   * 无参数命令直接执行，保留草稿；例如 /export 开始下载。
   * /goal、/agent 等需要参数的命令由 pick 替换当前触发词，继续编辑。
   */
  const runCommand = useCallback(
    (id: CommandId) => {
      switch (id) {
        case 'session.new':
          void startNewSession()
          break
        case 'composer.clear':
          setValue('')
          scheduleFrame(resize)
          break
        case 'model.select':
          // 与工具条上的 model seat 打开**同一个**菜单。
          setModelMenuOpen(true)
          break
        case 'mode.plan':
          selectMode('plan')
          break
        case 'mode.goal':
          selectMode('goal')
          break
        case 'view.theme':
          toggleTheme()
          break
        case 'session.export':
          if (sessionId) window.open(api.exportUrl(sessionId), '_blank', 'noopener')
          break
        case 'view.changes':
          showLatestChanges()
          break
        case 'view.tools':
          useAppStore.getState().setSettingsOpen(true, 'tools')
          break
        case 'view.todos':
        case 'view.context':
        case 'view.files':
          setInspectorTab(id.slice('view.'.length) as 'todos' | 'context' | 'files' | 'runtime')
          break
        case 'view.runtime':
          useAppStore.setState({ inspectorJobTarget: null })
          setInspectorTab('runtime')
          break
        case 'view.sessions':
          if (!useAppStore.getState().sidebarOpen) toggleSidebar()
          break
        case 'help.shortcuts':
          setShortcutsOpen(true)
          break
      }
    },
    [
      startNewSession,
      setValue,
      resize,
      scheduleFrame,
      selectMode,
      toggleTheme,
      sessionId,
      setInspectorTab,
      toggleSidebar,
      setShortcutsOpen,
    ],
  )

  const pick = useCallback(
    (item: ScoredCandidate) => {
      setTrigger(null)
      if (item.candidate.kind === 'command') {
        const command = item.candidate as CommandSpec
        if (command.id === 'mode.plan' || command.id === 'mode.goal') {
          selectMode(command.id === 'mode.plan' ? 'plan' : 'goal')
          if (trigger) setValue(valueRef.current.slice(0, trigger.from) + valueRef.current.slice(trigger.to))
          scheduleFrame(resize)
          return
        }
        if (!command.acceptsInput) {
          runCommand(command.id)
          return
        }
      }
      if (!trigger) return
      const result = applyCompletion(valueRef.current, item.candidate, trigger)
      pendingCompletion.current = { ...result, draftKey }
      setValue(result.text)
      valueRef.current = result.text
    },
    [trigger, runCommand, selectMode, setValue, draftKey, resize, scheduleFrame],
  )

  const selectCandidate = (item: ScoredCandidate) => setSelection({query: selectionQuery, candidate: candidateKey(item.candidate)})
  const handleCompletionKey = (event: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if (trigger) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault()
        if (items.length) {
          const step = event.key === 'ArrowDown' ? 1 : -1
          setSelection({ query: selectionQuery, candidate: candidateKey(items[(activeIndex + step + items.length) % items.length]!.candidate) })
        }
        return true
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault()
        const chosen = items[activeIndex]
        if (chosen) pick(chosen)
        else if (!files.loading) setTrigger(null)
        return true
      }
      if (event.key === 'Escape') {
        event.preventDefault()
        setTrigger(null)
        return true
      }
    }

    return false
  }
  return { trigger, setTrigger, triggerMenuId, closeTrigger, files, fileRoot, items, allItems, activeIndex, showMoreReferences, syncTrigger, pick, selectCandidate, handleCompletionKey }
}
