import {canIntegrateTeam} from '@/lib/teamIntegration'
import type {ResourceText} from '@/types'
import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import { useEffect, useRef, useState } from 'react'
import type { TeamBlock } from '@/types'
import { t, useLocale } from '@/i18n'
import { runtimeRequest, ApiError } from '@/lib/http'
import { api, unwrap } from '@/lib/api'
import { normalizePath, sessionDirectory } from '@/lib/workspaces'
import { useAppStore } from '@/store/app'
import { Button } from '@/components/ui/primitives'
import { WorktreeReview } from '@/components/overlays/WorktreeReview'

type Candidate = { id: string; path: string; branch?: string }
export function TeamIntegration({ block, sessionId }: { block: TeamBlock; sessionId: string }) {
  useLocale()
  const [candidate, setCandidate] = useState<Candidate | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [conflicts, setConflicts] = useState<string[]>([])
  const generation = useRef(0)
  useEffect(() => () => { generation.current++ }, [])
  async function integrate() {
    const current = ++generation.current
    setBusy(true); setError(''); setConflicts([])
    try {
      const result = await runtimeRequest<{worktree: Candidate; reused: boolean}>(`/sessions/${encodeURIComponent(sessionId)}/team-runs/${encodeURIComponent(block.runId)}/integration`, 'POST', {expected_seq: block.sourceSeq})
      if (generation.current === current) setCandidate(result.worktree)
    } catch (failure) {
      if (generation.current !== current) return
      setError(failureText(failure))
      if (failure instanceof ApiError && failure.code === 'git/integration-failed' && failure.details && typeof failure.details === 'object') {
        const details = failure.details as {worktree_id?: unknown; path?: unknown; cause?: {details?: {conflicts?: unknown}}}
        if (typeof details.worktree_id === 'string' && typeof details.path === 'string') setCandidate({id: details.worktree_id, path: details.path})
        if (Array.isArray(details.cause?.details?.conflicts)) setConflicts(details.cause.details.conflicts.filter((path): path is string => typeof path === 'string'))
      }
    } finally { if (generation.current === current) setBusy(false) }
  }
  async function open() {
    if (!candidate) return
    const current = ++generation.current
    setBusy(true)
    try {
      const store = useAppStore.getState()
      const existing = store.sessions.find(session => normalizePath(sessionDirectory(session)) === normalizePath(candidate.path))
      const id = existing?.id ?? (await unwrap(api.createSession({cwd: candidate.path}))).session.id
      await store.refreshSessions()
      if (generation.current === current && useAppStore.getState().activeSessionId === sessionId) useAppStore.getState().selectSession(id)
    } catch (failure) { if (generation.current === current) setError(failureText(failure)) }
    finally { if (generation.current === current) setBusy(false) }
  }
  return <section aria-label={t('ui.team.integration.heading')} aria-busy={busy} className="min-w-0 space-y-2 rounded-md border border-line bg-canvas/50 p-2">
    <p className="text-xs font-medium">{t('ui.team.integration.heading')}</p>
    <p className="text-2xs text-muted">{t('ui.team.integration.note')}</p>
    {!candidate && <Button size="sm" disabled={busy || !canIntegrateTeam(block)} onClick={() => void integrate()}>{t(busy ? 'ui.team.integration.busy' : 'ui.team.integration.create')}</Button>}
    {!candidate && !canIntegrateTeam(block) && <p className="text-2xs text-muted">{t('ui.team.integration.incomplete')}</p>}
    {error && <p role="alert" className="break-words text-xs text-danger">{noticeText(error)}</p>}
    {conflicts.map(path => <p key={path} className="break-all font-mono text-2xs text-danger">{path}</p>)}
    {candidate && <div className="min-w-0 space-y-2">
      <p className="break-all font-mono text-2xs text-muted">{candidate.path}</p>
      <Button size="sm" disabled={busy} onClick={() => void open()}>{t('ui.team.integration.open')}</Button>
      <WorktreeReview key={candidate.id} id={candidate.id} defaultOpen />
    </div>}
  </section>
}
