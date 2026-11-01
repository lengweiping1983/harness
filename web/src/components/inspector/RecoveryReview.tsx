import { failureText } from '@/lib/resourceError'
import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { Switch } from '@/components/ui/primitives'
import { t, useLocale } from '@/i18n'
import { useEffect, useRef, useState } from 'react'
import { Loader2, X } from 'lucide-react'
import type { RecoveryPreview, RecoveryTool, RunRecord } from '@/lib/protocol.generated'
import { continueRun, readRecoveryPreview, type RecoveryDraft } from '@/lib/runHistory'
import { ApiError } from '@/lib/http'
import { modelFailure } from '@/lib/modelFailure'
import { readRecoveryDraft, saveRecoveryDraft, clearRecoveryDraft, clearSubmittedRecoveryDraft, recoveryDraftUnpersisted, type RecoveryEditorDraft } from '@/lib/recoveryDraft'
import { copyText } from '@/lib/utils'
import { Button, Overlay, ShowMoreControl } from '@/components/ui/primitives'
import {RecoverySummary} from './RecoverySummary'

const statuses: Record<RecoveryTool['status'], string> = {
  get reported_success() { return t("ui.components.inspector.RecoveryReview.7263ac57a044") }, get reported_error() { return t("ui.components.inspector.RecoveryReview.b7247fd6808f") }, get unknown() { return t("ui.lib.projectHooks.e6bedaccc265") }, get not_executed() { return t("ui.components.inspector.RecoveryReview.f0af5479c221") },
}
const effects: Record<string, string> = { get read() { return t("ui.components.inspector.RecoveryReview.bcd6771e08ec") }, get workspace_write() { return t("ui.components.inspector.RecoveryReview.64963af86ff1") }, get external_write() { return t("ui.components.inspector.RecoveryReview.f5c4c8929916") }, get execute() { return t("ui.components.inspector.RecoveryReview.988af27c0cf2") }, get unknown() { return t("ui.components.inspector.RecoveryReview.f5e831f63031") } }

export function RecoveryReview({ sessionId, record, onClose, onSubmitted, onOriginal }: {
  sessionId: string; record: RunRecord; onClose: () => void; onSubmitted: (run: RunRecord) => void; onOriginal: (id: string) => void
}) {
  useLocale()

  const [preview, setPreview] = useState<RecoveryPreview | null>(null)
  const [readError, setReadError] = useState<ResourceText>('')
  const [actionError, setActionError] = useState<ResourceText>('')
  const [refresh, setRefresh] = useState(0)
  const [restored] = useState(() => readRecoveryDraft(sessionId, record.id))
  const emptyDraft = (): RecoveryEditorDraft => ({ mode: 'inspect', instructions: {}, note: '', sourceVersion: null })
  const [editor, setEditor] = useState<RecoveryEditorDraft>(() => restored ?? emptyDraft())
  const [hasDraft, setHasDraft] = useState(!!restored)
  const [storageFailed, setStorageFailed] = useState(() => recoveryDraftUnpersisted(sessionId, record.id))
  const mode = editor.mode, note = editor.note
  const drafts = { inspect: editor.instructions.inspect ?? preview?.inspection_text ?? '', continue: editor.instructions.continue ?? preview?.continuation_text ?? '' }
  const updateEditor = (next: RecoveryEditorDraft) => {
    setEditor(next); setHasDraft(true)
    setStorageFailed(!saveRecoveryDraft(sessionId, record.id, next))
  }
  const changeMode = (next: RecoveryEditorDraft['mode']) => {
    updateEditor({ ...editor, mode: next }); setActionError(''); setVerified(false)
  }
  const [verified, setVerified] = useState(false)
  const [posting, setPosting] = useState(false)
  const [paging, setPaging] = useState(false)
  const submitting = useRef<number | null>(null)
  const epoch = useRef(0)
  const pageRequest = useRef<AbortController>()
  useEffect(() => {
    const controller = new AbortController()
    ++epoch.current
    setPaging(false); setPosting(false); submitting.current = null
    setReadError(''); setPreview(null)
    void readRecoveryPreview(sessionId, record.id, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return
        setPreview(value); setVerified(false)
      }).catch((error: Error) => { if (!controller.signal.aborted) setReadError(failureText(error)) })
    return () => { ++epoch.current; controller.abort(); pageRequest.current?.abort(); pageRequest.current = undefined }
  }, [sessionId, record.id, refresh])
  const known = preview?.known_continuations[mode]
  const failure = modelFailure(preview?.error, record.error?.code)
  const canSubmit = preview && !readError && !known && drafts[mode].trim() && !posting &&
    (mode === 'inspect' || !preview.verification_required || verified && note.trim())
  async function submit() {
    if (!canSubmit || !preview || submitting.current !== null) return
    const ticket = epoch.current
    submitting.current = ticket
    setPosting(true); setActionError('')
    const draft: RecoveryDraft = { mode, text: drafts[mode], recovery_version: preview.version,
      verification_note: mode === 'continue' ? note : '' }
    try {
      const run = await continueRun(sessionId, record.id, draft)
      clearSubmittedRecoveryDraft(sessionId, record.id, mode, editor)
      if (ticket === epoch.current) onSubmitted(run)
    }
    catch (error) {
      if (ticket !== epoch.current) return
      setActionError(failureText(error))
      if (error instanceof ApiError && error.code === 'run/recovery-changed') setVerified(false)
    } finally { if (submitting.current === ticket) submitting.current = null; if (ticket === epoch.current) setPosting(false) }
  }
  async function loadMore() {
    if (!preview || preview.next_offset == null || paging || pageRequest.current) return
    const ticket = epoch.current
    const controller = new AbortController()
    pageRequest.current = controller
    setPaging(true); setReadError('')
    try {
      const page = await readRecoveryPreview(sessionId, record.id, controller.signal, preview)
      if (ticket !== epoch.current || controller.signal.aborted) return
      setPreview({ ...page, tools: [...preview.tools, ...page.tools] })
    } catch (error) {
      if (ticket === epoch.current && !controller.signal.aborted) {
        setReadError(failureText(error)); setVerified(false)
      }
    }
    finally { if (pageRequest.current === controller) pageRequest.current = undefined; if (ticket === epoch.current) setPaging(false) }
  }
  return <Overlay className="max-w-screen-sm" labelledBy="recovery-title" onClose={() => { if (!posting) onClose() }}>
    <div className="flex max-h-recovery w-full flex-col rounded-2xl border border-line bg-canvas shadow-pop" aria-label={t("ui.components.inspector.RecoveryReview.5fd9cda3c43c")}>
      <header className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div><h2 id="recovery-title" className="text-base font-semibold">{t("ui.components.inspector.RecoveryReview.9d5d684c5de3")}</h2><p className="mt-1 text-xs text-muted">{t("ui.components.inspector.RecoveryReview.dae954958717")}</p></div>
        <Button aria-label={t("ui.components.inspector.RecoveryReview.ddc73c64138d")} variant="ghost" size="sm" disabled={posting} onClick={onClose} icon={<X className="h-4 w-4" />} />
      </header>
      <div className="min-h-0 space-y-4 overflow-y-auto px-5 py-4 text-sm">
        {readError ? <div role="alert" className="space-y-2 text-xs text-danger"><p>{noticeText(readError)}</p><Button size="sm" disabled={posting} onClick={() => setRefresh((value) => value + 1)}>{t("ui.components.inspector.RecoveryReview.a4003263a248")}</Button></div> : null}
        {!preview && !readError ? <p role="status" className="flex items-center gap-2 text-muted"><Loader2 className="h-4 w-4 animate-spin" />{t("ui.components.inspector.RecoveryReview.fb770ca291e0")}</p> : null}
        {storageFailed && !hasDraft ? <p role="alert" className="text-xs leading-5 text-warn">{t("ui.components.inspector.RecoveryReview.2aaaadbc71a8")}</p> : null}
        {preview ? <>
          <RecoverySummary preview={preview} />
          {hasDraft ? <div role="status" className="space-y-1 rounded-lg bg-raised px-3 py-2 text-xs leading-5 text-muted"><p>{storageFailed ? t("ui.components.inspector.RecoveryReview.09232c65d126") : t("ui.components.inspector.RecoveryReview.af82918dfd29")}</p>{editor.sourceVersion && editor.sourceVersion !== preview.version ? <p className="text-warn">{t("ui.components.inspector.RecoveryReview.1cd42f27ac8e")}</p> : null}{mode === 'continue' && preview.verification_required ? <p>{t("ui.components.inspector.RecoveryReview.cdf89ccd2f46")}</p> : null}<Button size="xs" variant="ghost" onClick={() => void copyText(drafts[mode] + (note ? t("ui.components.inspector.RecoveryReview.9f12dfc763ce") + note : ''))}>{t("ui.components.inspector.RecoveryReview.7acd9daa8d25")}</Button></div> : null}
          <section aria-label={t("ui.components.inspector.RecoveryReview.41de232ac182")}><div className="flex items-center justify-between gap-2"><h3 className="text-xs font-medium text-muted">{t("ui.components.inspector.RecoveryReview.693e3009482b")}</h3><Button size="sm" variant="ghost" disabled={posting} onClick={() => onOriginal(record.id)}>{t("ui.components.inspector.RecoveryReview.272e7a151c04")}</Button></div><p className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words leading-6">{preview.task}</p>{preview.task_truncated ? <p className="mt-1 text-xs text-muted">{t("ui.components.inspector.RecoveryReview.cd52112b52bc")}</p> : null}<p className="mt-2 break-words text-xs leading-5 text-danger">{(preview.error_copy ? noticeText(preview.error_copy) : failure?.title || preview.error) || t("ui.components.inspector.RecoveryReview.2784cdbfd8a2")}</p>{failure ? <details className="mt-1 text-xs leading-5 text-muted"><summary className="cursor-pointer">{t("ui.components.inspector.RecoveryReview.c26fb419db3c")}</summary><p className="whitespace-pre-wrap break-words">{preview.error}</p><p>{failure.hint}</p></details> : null}</section>
          <section aria-label={t("ui.components.inspector.RecoveryReview.0a6e55564f60")}><h3 className="text-xs font-medium text-muted">{t('ui.recovery.recordsTitle')}</h3>
            <p className="mt-1 text-xs leading-5 text-muted">{t("ui.components.inspector.RecoveryReview.7d9d4a0a083a")}</p>
            {preview.scope_known && !preview.total_tools ? <p className="mt-2 text-xs text-muted">{t("ui.components.inspector.RecoveryReview.00b5aa871848")}</p> : null}
            <ul className="mt-2 divide-y divide-line">{preview.tools.map((tool) => <li key={[tool.source_seq, tool.call_id].join(":")} className="py-2"><details><summary className="cursor-pointer text-xs leading-5"><span className="break-all font-medium">{tool.name}</span><span className="ml-2 text-muted">{effects[tool.effect] || t("ui.components.inspector.RecoveryReview.1837035744c3")} · {statuses[tool.status]}</span></summary><div className="mt-2 space-y-2 text-xs leading-5 text-muted"><p>{t("ui.components.inspector.RecoveryReview.df311454da96")}{tool.idempotent ? t("ui.components.inspector.RecoveryReview.b5141d3d19e9") : t("ui.components.inspector.RecoveryReview.181cd977c53e")}{t("ui.components.inspector.RecoveryReview.da615da23dda")}</p>{tool.arguments ? <details><summary className="cursor-pointer">{t("ui.components.inspector.RecoveryReview.93410c30cb28")}</summary><pre className="mt-1 overflow-auto whitespace-pre-wrap break-all rounded bg-raised p-2">{tool.arguments}</pre></details> : null}<p className="whitespace-pre-wrap break-words">{tool.result || t("ui.components.inspector.RecoveryReview.937ecb89acad")}</p>{tool.result_truncated ? <p>{t("ui.components.inspector.RecoveryReview.2782321b7291")}</p> : null}</div></details></li>)}</ul>
            {preview.next_offset != null ? <ShowMoreControl disabled={paging} onClick={() => void loadMore()}>{paging ? t("ui.components.inspector.RecoveryReview.86b6d0d63062") : t("ui.showMore")}</ShowMoreControl> : null}
          </section>
          <fieldset disabled={posting} className="space-y-2"><legend className="mb-2 text-xs font-medium text-muted">{t("ui.components.inspector.RecoveryReview.9e792c8ef798")}</legend>
            <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-line px-3 py-2"><input type="radio" name="recovery-mode" value="inspect" checked={mode === 'inspect'} onChange={() => changeMode('inspect')} className="mt-1" /><span><span className="text-sm font-medium">{t("ui.components.inspector.RecoveryReview.d95997e29a1b")}</span><span className="mt-1 block text-xs leading-5 text-muted">{t("ui.components.inspector.RecoveryReview.a7f22ac2e7f9")}</span></span></label>
            <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-line px-3 py-2"><input type="radio" name="recovery-mode" value="continue" checked={mode === 'continue'} onChange={() => changeMode('continue')} className="mt-1" /><span><span className="text-sm font-medium">{t("ui.components.inspector.RecoveryReview.cae781c75252")}</span><span className="mt-1 block text-xs leading-5 text-muted">{t("ui.components.inspector.RecoveryReview.c2850cccce75")}</span></span></label>
          </fieldset>
          {known ? <div className="space-y-2 text-xs leading-5 text-muted"><p>{t("ui.components.inspector.RecoveryReview.ab5899a78316")}</p><Button size="sm" onClick={() => onOriginal(String(known))}>{t("ui.components.inspector.RecoveryReview.61de4f09e2be")}</Button></div> : <>
            <label className="block text-xs font-medium" htmlFor="recovery-instruction">{t("ui.components.inspector.RecoveryReview.dee7899ccd7f")}</label><textarea id="recovery-instruction" className="min-h-24 w-full resize-y rounded-lg border border-line bg-canvas p-3 text-sm leading-6" disabled={posting} value={drafts[mode]} onChange={(event) => updateEditor({ ...editor, sourceVersion: editor.sourceVersion ?? preview.version, instructions: { ...editor.instructions, [mode]: event.target.value } })} />
            {mode === 'continue' && preview.verification_required ? <div className="space-y-2 rounded-lg border border-warn/40 p-3 text-xs leading-5"><p className="text-warn">{preview.uncertain_operations ? t("ui.components.inspector.RecoveryReview.b7ef2a566e66", {p0: (preview.uncertain_operations)}) : t("ui.components.inspector.RecoveryReview.c3d5a4f65a15")}{t("ui.components.inspector.RecoveryReview.370bf8993810")}</p><label className="flex items-start gap-2"><Switch checked={verified} disabled={posting} onChange={(event) => setVerified(event.target.checked)} className="mt-1" /><span>{t("ui.components.inspector.RecoveryReview.6dbd3e6e183d")}</span></label><label htmlFor="recovery-note" className="block">{t("ui.components.inspector.RecoveryReview.1c4ef2d149e2")}</label><textarea id="recovery-note" value={note} maxLength={4000} disabled={posting} onChange={(event) => updateEditor({ ...editor, sourceVersion: editor.sourceVersion ?? preview.version, note: event.target.value })} className="min-h-16 w-full rounded border border-line bg-canvas p-2" placeholder={t("ui.components.inspector.RecoveryReview.5be6bd6caefb")} /></div> : null}
          </>}
          {actionError ? <div role="alert" className="space-y-2 text-xs text-danger"><p>{noticeText(actionError)}</p><Button size="sm" disabled={posting} onClick={() => { setActionError(''); setRefresh((value) => value + 1) }}>{t("ui.components.inspector.RecoveryReview.63ca2f5e52a1")}</Button></div> : null}
        </> : null}
      </div>
      <footer className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-3">{hasDraft ? <Button size="sm" variant="ghost" disabled={posting} onClick={() => { setStorageFailed(!clearRecoveryDraft(sessionId, record.id)); setEditor(emptyDraft()); setHasDraft(false); setVerified(false); setActionError('') }}>{t("ui.components.inspector.RecoveryReview.70403791a994")}</Button> : null}<Button size="sm" variant="ghost" disabled={posting} onClick={onClose}>{t("ui.components.inspector.RecoveryReview.2cd0f3be8738")}</Button><Button size="sm" variant="primary" disabled={!canSubmit} onClick={() => void submit()}>{posting ? t("ui.components.inspector.RecoveryReview.d6a7fb87cac5") : mode === 'inspect' ? t("ui.components.inspector.RecoveryReview.11a3ff2c9eaf") : t("ui.components.inspector.RecoveryReview.0ae5c3b7ccd1")}</Button></footer>
    </div>
  </Overlay>
}
