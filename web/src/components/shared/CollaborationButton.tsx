import {ApiError, type ApiResult} from '@/lib/http'
import {failureText} from '@/lib/resourceError'
import {collaborationFeedbackMessage, type CollaborationFeedback} from '@/lib/collaborationError'
import {useCallback, useEffect, useLayoutEffect, useRef, useState} from 'react'
import {UserPlus} from 'lucide-react'
import {Button, CopyButton, Overlay, RefreshButton} from '@/components/ui/primitives'
import {collaboration, type MemberRole} from '@/lib/collaboration'
import type {SessionCollaboration} from '@/lib/protocol.generated'
import {sharingOrigin} from '@/lib/sharedAccess'
import {t, useLocale} from '@/i18n'
import {usePageVisible} from '@/hooks/usePageVisible'

function operationFeedback(error: unknown): CollaborationFeedback {
  if (error instanceof ApiError) return error.failure
  const text = failureText(error)
  return {code: 'collaboration/operation-failed', message: typeof text === 'string' ? text : '',
    ...(typeof text === 'string' ? {} : {copy: text})}
}

export function CollaborationButton({sessionId, open, onOpen, onClose}: {sessionId: string | null; open: boolean; onOpen(): void; onClose(): void}) {
  useLocale()
  return <><Button size="sm" variant="ghost" className="shrink-0" icon={<UserPlus size={14} aria-hidden="true" />} onClick={onOpen}>{t('ui.collaboration.invite')}</Button>
    {open && sessionId && <Members key={sessionId} sessionId={sessionId} close={onClose} />}</>
}
function Members({sessionId, close}: {sessionId: string; close: () => void}) {
  const [data, setData] = useState<SessionCollaboration | null>(null)
  const [failure, setFailure] = useState<CollaborationFeedback>(null)
  const [disabled, setDisabled] = useState(false)
  const [busy, setBusy] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const visible = usePageVisible()
  const readAbort = useRef<AbortController | null>(null)
  const [role, setRole] = useState<MemberRole>('collaborator')
  const [link, setLink] = useState('')
  const current = useRef(true)
  const operationLock = useRef(false)
  const generation = useRef(0)
  const refresh = useCallback(async () => {
    if (!current.current || document.hidden) return
    readAbort.current?.abort()
    const controller = new AbortController()
    readAbort.current = controller
    const ticket = ++generation.current
    setRefreshing(true)
    try {
      const result = await collaboration.state(sessionId, controller.signal)
      if (!current.current || controller.signal.aborted || ticket !== generation.current) return
      setDisabled(!result.ok && result.code === 'access/not-enabled')
      if (result.ok) {setData(result.data); setFailure(null)}
      else setFailure(result.code === 'access/not-enabled' ? {key: 'ui.collaboration.disabled'} : result)
    } catch (error) {
      if (current.current && !controller.signal.aborted && ticket === generation.current) setFailure(operationFeedback(error))
    } finally {
      if (current.current && !controller.signal.aborted && ticket === generation.current) {
        readAbort.current = null
        setRefreshing(false)
      }
    }
  }, [sessionId])
  useLayoutEffect(() => {
    current.current = true
    return () => {current.current = false; generation.current++; readAbort.current?.abort()}
  }, [])
  useEffect(() => {
    if (!visible) {setRefreshing(false); return}
    void refresh()
    const timer = window.setInterval(() => {if (!operationLock.current && !readAbort.current) void refresh()}, 5000)
    return () => {generation.current++; readAbort.current?.abort(); readAbort.current = null; clearInterval(timer)}
  }, [refresh, visible])
  async function mutate<T>(action: () => Promise<ApiResult<T>>, accepted?: (value: T) => void) {
    if (!current.current || operationLock.current) return
    operationLock.current = true
    generation.current++
    readAbort.current?.abort()
    readAbort.current = null
    setRefreshing(false)
    setBusy(true); setFailure(null)
    try {
      const result = await action()
      if (!current.current) return
      if (result.ok) accepted?.(result.data)
      await refresh()
      if (!result.ok && current.current) setFailure(result)
    } catch (error) {
      if (current.current) {
        await refresh()
        if (current.current) setFailure(operationFeedback(error))
      }
    } finally {
      operationLock.current = false
      if (current.current) setBusy(false)
    }
  }
  async function revoke(identifier: string) {
    const result = await collaboration.revoke(sessionId, identifier)
    if (current.current && result.ok && link.includes(`.${identifier}.`)) setLink('')
    return result
  }
  async function invite() {
    const shareOrigin = sharingOrigin(data?.origin || '')
    if (!shareOrigin) {setFailure({key: 'ui.collaboration.originError'}); return}
    await mutate(() => collaboration.invite(sessionId, role), invitation => {
      setLink(`${shareOrigin}/shared#token=${encodeURIComponent(invitation.token)}`)
    })
  }
  return <Overlay onClose={close} labelledBy="members-title" className="max-w-xl"><section aria-busy={refreshing} className="refresh-action-container max-h-collaboration space-y-4 overflow-y-auto p-5 text-sm">
    <div className="flex items-center justify-between gap-2"><h2 id="members-title" className="text-xl font-semibold">{t('ui.collaboration.title')}</h2>
      {data ? <RefreshButton size="xs" variant="ghost" label={t('ui.collaboration.refresh')} loading={refreshing} disabled={busy || refreshing} onClick={() => void refresh()} /> : null}</div>
    <p className="text-muted">{t('ui.collaboration.history')}</p>
    {failure && <p role="alert" className="text-danger">{collaborationFeedbackMessage(failure)}</p>}
    {!data ? disabled ? <Button disabled={busy} onClick={() => void mutate(() => collaboration.enable(sessionId))}>{t('ui.collaboration.enable')}</Button> : <RefreshButton variant="ghost" label={t('ui.collaboration.refresh')} onClick={() => void refresh()} /> : <>
      <div className="flex items-end gap-2"><label className="min-w-0 flex-1">{t('ui.collaboration.origin')}<input readOnly value={data.origin || ''} className="mt-2 w-full rounded border border-line bg-raised p-2" /></label>
        {data.origin ? <CopyButton key={data.origin} text={data.origin} label={t('ui.collaboration.copyOrigin')} /> : null}</div>
      {!data.origin && <p role="status" className="text-muted">{t('ui.collaboration.networkRequired')}</p>}
      <label className="flex items-center gap-3">{t('ui.collaboration.invitationRole')}<select value={role} onChange={e => setRole(e.target.value as MemberRole)} className="rounded border border-line bg-raised p-2">{(['observer','collaborator','administrator'] as const).map(value => <option key={value} value={value}>{t(`ui.collaboration.${value}`)}</option>)}</select></label>
      <Button disabled={busy || !data.origin} onClick={() => void invite()}>{t('ui.collaboration.create')}</Button>
      {link && <div className="flex items-end gap-2"><label className="min-w-0 flex-1">{t('ui.collaboration.link')}<input readOnly value={link} onFocus={e => e.target.select()} className="mt-2 w-full rounded border border-line bg-raised p-2" /></label>
        <CopyButton key={link} text={link} label={t('ui.collaboration.copyLink')} /></div>}
      {data.invitations.filter(row => row.status === 'pending' || row.status === 'open').map(row => <div key={row.id} className="flex flex-wrap items-center gap-3 border-t border-line py-2"><span className="flex-1">{row.display_name || row.id.slice(0, 8)} · {t(`ui.collaboration.${row.role}`)}</span>
        {row.status === 'pending' && <Button disabled={busy} onClick={() => void mutate(() => collaboration.approve(sessionId, row.id, data.access.revision))}>{t('ui.collaboration.approve')}</Button>}
        <Button disabled={busy} onClick={() => void mutate(() => revoke(row.id))}>{t('ui.collaboration.revoke')}</Button></div>)}
      {Object.entries(data.access.members).map(([subject, caps]) => <div key={subject} className="flex flex-wrap items-center gap-3 border-t border-line py-2"><span className="min-w-0 flex-1 break-all">{subject === data.access.owner && subject === 'local-owner' ? t('ui.collaboration.localOwner') : <>{data.access.profiles[subject]?.display_name || subject} · {subject.slice(-8)}</>}</span>
        <select aria-label={t('ui.collaboration.memberRole', {name: subject === data.access.owner && subject === 'local-owner' ? t('ui.collaboration.localOwner') : data.access.profiles[subject]?.display_name || subject})} disabled={busy || subject === data.access.owner} value={caps.includes('manage') ? 'administrator' : caps.includes('contribute') ? 'collaborator' : 'observer'} onChange={e => void mutate(() => collaboration.member(sessionId, subject, e.target.value as MemberRole, data.access.revision))} className="rounded border border-line bg-raised p-2">{(['observer','collaborator','administrator'] as const).map(value => <option key={value} value={value}>{t(`ui.collaboration.${value}`)}</option>)}</select>
        {subject !== data.access.owner && <Button disabled={busy} onClick={() => void mutate(() => collaboration.member(sessionId, subject, 'removed', data.access.revision))}>{t('ui.collaboration.remove')}</Button>}</div>)}
    </>}
    <Button variant="ghost" onClick={close}>{t('ui.collaboration.close')}</Button>
  </section></Overlay>
}
