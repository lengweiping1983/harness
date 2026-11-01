import {collaborationFeedbackMessage, type CollaborationFeedback} from '@/lib/collaborationError'
import {useEffect, useRef, useState} from 'react'
import {Button, RefreshButton} from '@/components/ui/primitives'
import {readInvitationRecovery, saveInvitationRecovery, clearInvitationRecovery} from '@/lib/invitationRecovery'
import {collaboration} from '@/lib/collaboration'
import {t, useLocale} from '@/i18n'
import {useAppTheme} from '@/hooks/useAppTheme'
import {SharedSessionPage} from './SharedSessionPage'
import {usePageVisible} from '@/hooks/usePageVisible'

function secret() {
  return btoa(String.fromCharCode(...crypto.getRandomValues(new Uint8Array(32)))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','')
}
export function InvitationPage({token}: {token: string}) {
  useLocale(); useAppTheme()
  const visible = usePageVisible()
  const [name, setName] = useState('')
  const [recovery] = useState(() => {const row = readInvitationRecovery(); return row?.token === token ? row : undefined})
  const [claimSecret] = useState(() => recovery?.secret ?? secret())
  const [status, setStatus] = useState(recovery ? 'pending' : 'new')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<CollaborationFeedback>(null)
  const [entered, setEntered] = useState(false)
  const alive = useRef(true)
  const readEpoch = useRef(0)
  const reading = useRef(0)
  useEffect(() => {alive.current = true; return () => {alive.current = false}}, [])
  async function check(signal?: AbortSignal) {
    const ticket = ++readEpoch.current
    reading.current++
    try {
    const result = await collaboration.status(token, claimSecret, signal)
    if (!alive.current || signal?.aborted || ticket !== readEpoch.current) return
    if (result.ok) {setStatus(result.data.status); setFailure(null); if (result.data.status === 'revoked') clearInvitationRecovery(token)}
    else {
      // 服务端明确没有匹配的领取证明时恢复表单；仍须用户主动申请。
      if (result.code === 'invitation/invalid-token') setStatus('new')
      setFailure(result)
    }
    } finally {reading.current--}
  }
  useEffect(() => {
    if (!recovery || !visible) return
    const controller = new AbortController()
    void check(controller.signal)
    return () => controller.abort()
  }, [recovery, visible])
  useEffect(() => {
    if (status !== 'pending' || !visible) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      if (!reading.current) await check(controller.signal)
      if (!controller.signal.aborted) timer = setTimeout(poll, 5000)
    }
    timer = setTimeout(poll, 5000)
    return () => {controller.abort(); clearTimeout(timer)}
  }, [status, token, claimSecret, visible])
  async function claim() {
    setBusy(true); setFailure(null)
    saveInvitationRecovery(token, claimSecret)
    const result = await collaboration.claim(token, name, claimSecret)
    if (!alive.current) return
    setBusy(false)
    if (result.ok) setStatus(result.data.status)
    else {await check(); if (alive.current) setFailure(result)}
  }
  async function enter() {
    setBusy(true)
    const result = await collaboration.enter(token, claimSecret)
    // 兑换结果未知只回查Cookie身份，不自动重放签发。
    const proof = await collaboration.status(token, claimSecret)
    const access = await import('@/lib/api').then(({api}) => api.accessStatus())
    if (!alive.current) return
    setBusy(false)
    if (access.ok && access.data.authenticated && result.ok && access.data.subject === result.data.subject) setEntered(true)
    else if (proof.ok && proof.data.status === 'approved' && proof.data.subject && access.ok && access.data.authenticated && access.data.subject === proof.data.subject && access.data.session_ids.includes(token.split('.')[1])) setEntered(true)
    else setFailure(result.ok ? {key: 'ui.collaboration.failed'} : result)
  }
  useEffect(() => {if (entered) clearInvitationRecovery(token)}, [entered, token])
  if (entered) return <SharedSessionPage initialToken="" />
  return <main className="flex min-h-dvh items-center justify-center bg-surface p-5 text-ink"><section className="w-full max-w-md space-y-4 rounded-xl border border-line bg-raised p-6 text-sm">
    <h1 className="text-xl font-semibold">{t('ui.collaboration.join')}</h1><p className="text-muted">{t('ui.collaboration.joinHint')}</p>
    {failure && <p role="alert" className="text-danger">{collaborationFeedbackMessage(failure)}</p>}
    {status === 'new' && <><label className="block">{t('ui.collaboration.name')}<input autoComplete="name" value={name} maxLength={80} onChange={e => setName(e.target.value)} className="mt-2 w-full rounded border border-line bg-surface p-2" /></label><Button disabled={busy || !name.trim()} onClick={() => void claim()}>{t('ui.collaboration.apply')}</Button></>}
    {status === 'pending' && <p role="status">{t('ui.collaboration.pending')}</p>}
    {status === 'approved' && <Button disabled={busy} onClick={() => void enter()}>{t('ui.collaboration.enter')}</Button>}
    {status === 'revoked' && <p role="status">{t('ui.collaboration.revoked')}</p>}
    {status !== 'new' && <RefreshButton variant="ghost" label={t('ui.collaboration.refresh')} disabled={busy} onClick={() => void check()} />}
  </section></main>
}
