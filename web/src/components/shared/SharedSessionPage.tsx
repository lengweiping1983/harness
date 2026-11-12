import {SecretInput} from '@/components/ui/SecretInput'
import {collaborationFeedbackMessage} from '@/lib/collaborationError'
import {answerSharedQuestion} from '@/lib/sharedDecisions'
import {SharedTaskInput} from './SharedTaskInput'
import {ViewerIdentityContext} from '../chat/ViewerIdentityContext'
import {useCallback, useEffect, useMemo, useRef, useState} from 'react'
import {Globe, Loader2, LogOut, Moon, RefreshCw} from 'lucide-react'
import {api} from '@/lib/api'
import type {ApiFailure, ApiResult} from '@/lib/http'
import type {BrowserAccessStatus} from '@/lib/protocol.generated'
import type {SessionSummary} from '@/types'
import {sharedCredentialId, takeSharedToken} from '@/lib/sharedAccess'
import {sessionTitle} from '@/lib/sessionTitle'
import {mainConversationBlocks} from '@/lib/memberDock'
import {useAppTheme} from '@/hooks/useAppTheme'
import {useSessionSocket} from '@/hooks/useSessionSocket'
import {useAppStore} from '@/store/app'
import {displayBlocks, useChat, useChatStore} from '@/store/chat'
import {setLocale, t, useLocale} from '@/i18n'
import {Button} from '@/components/ui/primitives'
import {ConversationPane} from '@/components/chat/ConversationPane'
import {SessionRunningIndicator} from '@/components/chat/SessionRunningIndicator'
import {Toaster} from '@/components/overlays/CommandPalette'

async function enter(token: string): Promise<ApiResult<BrowserAccessStatus>> {
  const before = await api.accessStatus()
  if (!before.ok || !before.data.enabled) return before
  const expected = await sharedCredentialId(token)
  const submitted = await api.accessLogin(token)
  // 即使响应丢失也只读回 Cookie 摘要，不自动重发密钥。
  const status = await api.accessStatus()
  if (status.ok && status.data.authenticated && status.data.credential_id === expected) return status
  if (!submitted.ok) return submitted
  if (!status.ok) return status
  return {ok: false, status: 401, code: 'access/unauthenticated', message: ''}
}

export function SharedSessionPage({initialToken}: {initialToken: string}) {
  useAppTheme()
  const locale = useLocale()
  const [token, setToken] = useState(initialToken)
  const [access, setAccess] = useState<BrowserAccessStatus | null>(null)
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)
  const [failure, setFailure] = useState<ApiFailure | null>(null)
  const [verified, setVerified] = useState(false)
  const [visible, setVisible] = useState(() => !document.hidden)
  const generation = useRef(0)
  const mutation = useRef<Promise<unknown>>(Promise.resolve())
  const enqueue = useCallback(<T,>(operation: () => Promise<T>): Promise<T> => {
    const next = mutation.current.then(operation, operation)
    mutation.current = next.catch(() => undefined)
    return next
  }, [])
  const owned = useRef(new Set<string>())
  const selectedId = useRef<string | null>(null)
  const first = useRef<Promise<ApiResult<BrowserAccessStatus>> | null>(null)
  const authorized = Boolean(access?.enabled && access.authenticated && verified)
  const channel = useSessionSocket(authorized && visible ? activeId : null, true)
  const chat = useChat(authorized ? activeId : null)
  const blocks = useMemo(() => mainConversationBlocks(displayBlocks(chat)).filter(block =>
    block.kind === 'user' || block.kind === 'assistant' || block.kind === 'question' || block.kind === 'error'), [chat, locale])

  const clear = useCallback(() => {
    for (const id of owned.current) useChatStore.getState().drop(id)
    owned.current.clear()
    useAppStore.setState({activeSessionId: null})
    selectedId.current = null
    setActiveId(null)
    setSessions([])
    setVerified(false)
  }, [])
  const accept = useCallback(async (result: ApiResult<BrowserAccessStatus>, ticket: number) => {
    if (ticket !== generation.current) return
    if (!result.ok) {setFailure(result); setBusy(false); return}
    setAccess(result.data)
    if (!result.data.enabled || !result.data.authenticated) {
      if (result.data.enabled && owned.current.size) setFailure({ok: false, status: 401, code: 'access/unauthenticated', message: ''})
      clear(); setBusy(false); return
    }
    const listing = await api.listSessions()
    if (ticket !== generation.current) return
    if (!listing.ok) {
      if (listing.status === 401 || listing.status === 403) {clear(); setAccess({...result.data, authenticated: false})}
      setFailure(listing); setBusy(false); return
    }
    const rows = listing.data.sessions
    setSessions(rows)
    const selected = rows.some(row => row.id === selectedId.current) ? selectedId.current : rows[0]?.id ?? null
    selectedId.current = selected
    if (selected) owned.current.add(selected)
    useAppStore.setState({activeSessionId: selected})
    setActiveId(selected)
    setVerified(true); setToken(''); setBusy(false); setFailure(null)
  }, [clear])
  const refresh = useCallback(() => {
    const ticket = ++generation.current
    setBusy(true); setFailure(null)
    void api.accessStatus().then(result => accept(result, ticket))
  }, [accept])
  useEffect(() => {
    const ticket = ++generation.current
    first.current ??= initialToken ? enqueue(() => enter(initialToken)) : api.accessStatus()
    void first.current.then(result => accept(result, ticket)).catch(() => {
      if (ticket === generation.current) {setBusy(false); setFailure({ok: false, status: 0, code: 'access/browser', message: ''})}
    })
    return () => {generation.current++}
  }, [accept, initialToken, enqueue])
  useEffect(() => {
    const changed = () => {
      setVisible(!document.hidden)
      if (document.hidden) {generation.current++; setVerified(false)}
      else refresh()
    }
    document.addEventListener('visibilitychange', changed)
    return () => document.removeEventListener('visibilitychange', changed)
  }, [refresh])
  useEffect(() => {
    if (visible && access?.authenticated && (channel.connection === 'reconnecting' || channel.connection === 'closed')) refresh()
  }, [channel.connection, visible, access?.authenticated, refresh])
  useEffect(() => () => {
    for (const id of owned.current) useChatStore.getState().drop(id)
    useAppStore.setState({activeSessionId: null})
  }, [])

  const openToken = useCallback(async (value: string) => {
    if (!value.trim()) return
    const ticket = ++generation.current
    clear(); setToken(value); setBusy(true); setFailure(null)
    try {await accept(await enqueue(() => enter(value.trim())), ticket)}
    catch {if (ticket === generation.current) {setBusy(false); setFailure({ok: false, status: 0, code: 'access/browser', message: ''})}}
  }, [accept, clear, enqueue])
  useEffect(() => {
    const changed = () => {void openToken(takeSharedToken())}
    window.addEventListener('hashchange', changed)
    return () => window.removeEventListener('hashchange', changed)
  }, [openToken])
  const signIn = (event: React.FormEvent) => {
    event.preventDefault()
    if (!busy) void openToken(token)
  }
  const signOut = async () => {
    if (busy) return
    const ticket = ++generation.current
    clear(); setBusy(true)
    const {submitted, status} = await enqueue(async () => {
      const submitted = await api.accessLogout()
      return {submitted, status: await api.accessStatus()}
    })
    if (ticket !== generation.current) return
    if (status.ok && !status.data.authenticated) {clear(); setAccess(status.data); setFailure(null); setToken('')}
    else setFailure(!submitted.ok ? submitted : !status.ok ? status : {ok: false, status: 0, code: 'access/logout-unknown', message: ''})
    setBusy(false)
  }
  const updateCapabilities = useCallback((capabilities: string[]) => {
    if (!activeId || selectedId.current !== activeId) return
    setAccess(current => {
      if (!current?.authenticated || current.subject !== access?.subject) return current
      if (JSON.stringify(current.capabilities?.[activeId]) === JSON.stringify(capabilities)) return current
      const next = {...current.capabilities, [activeId]: capabilities}
      return {...current, capabilities: next, read_only: !Object.values(next).some(values => values.includes('contribute'))}
    })
  }, [activeId, access?.subject])
  const answerQuestion = useCallback(async (questionId: string, answers: unknown) => {
    if (!activeId || !access?.subject) return
    const session = activeId
    try {await answerSharedQuestion(session, access.subject, questionId, answers)}
    finally {if (selectedId.current === session) channel.refreshHistory()}
  }, [activeId, access?.subject, channel.refreshHistory])
  const error = failure ? failure.code.startsWith('access/') || failure.status === 401 || failure.status === 403
    ? t(failure.code === 'access/logout-unknown' ? 'ui.shared.logoutUnknown' : 'ui.shared.invalid') : collaborationFeedbackMessage(failure.message ? failure : {key: 'ui.shared.unavailable'}) : null
  const controls = <>
    <Button size="sm" variant="ghost" aria-label={t('ui.shared.language')} onClick={() => setLocale(locale === 'zh-CN' ? 'en' : 'zh-CN')} icon={<Globe className="h-4 w-4" />}>{t('ui.shared.language')}</Button>
    <Button size="sm" variant="ghost" aria-label={t('ui.shared.theme')} onClick={() => useAppStore.getState().toggleTheme()} icon={<Moon className="h-4 w-4" />} />
    <Button size="sm" variant="ghost" aria-label={t('ui.shared.refresh')} disabled={busy} onClick={() => {refresh(); if (channel.connection === 'open') channel.refreshHistory(); else if (channel.connection === 'closed') channel.reconnect()}} icon={<RefreshCw className="h-4 w-4" />} />
    <Button size="sm" variant="ghost" disabled={busy} onClick={() => void signOut()} aria-label={t('ui.shared.leave')} title={t('ui.shared.leave')} icon={<LogOut className="h-4 w-4" />} />
  </>
  if (authorized && visible) return <div className="flex h-dvh min-h-0 flex-col bg-canvas text-ink" data-shared-session="">
    <ViewerIdentityContext.Provider value={access?.subject ?? null}><ConversationPane
      title={sessionTitle(sessions.find(row => row.id === activeId))}
      titleIndicator={<SessionRunningIndicator sessionId={activeId} />}
      headerTrailing={controls}
      empty={chat.hydrated && blocks.length === 0}
      notice={<>
        <p role="status" className="sr-only">{t(access?.read_only === false ? 'ui.shared.contributing' : 'ui.shared.readOnly')}</p>
        {error && <div role="alert" className="border-b border-danger/20 bg-danger/[0.12] px-4 py-3 text-sm text-danger">{error}</div>}
        {sessions.length > 1 && <label className="flex items-center gap-3 px-4 py-2 text-sm">{t('ui.shared.conversation')}
          <select className="min-w-0 flex-1 rounded-lg border border-line bg-raised px-3 py-2" aria-label={t('ui.shared.conversation')} value={activeId ?? ''} onChange={event => {
            const id = event.target.value; selectedId.current = id; owned.current.add(id); useAppStore.setState({activeSessionId: id}); setActiveId(id)
          }}>{sessions.map(row => <option key={row.id} value={row.id}>{sessionTitle(row)}</option>)}</select></label>}
      </>}
      conversation={activeId && chat.hydrated ? {
        readOnly: true, answerQuestion: access?.capabilities?.[activeId]?.includes('control') ? answerQuestion : undefined,
        blocks, history: chat.history, running: chat.running, streaming: Boolean(chat.draft.text),
        connection: channel.connection, onRefreshHistory: channel.refreshHistory,
      } : undefined}
    >
      {activeId && !chat.hydrated ? <div role="status" className="flex flex-1 items-center justify-center gap-3 text-sm text-muted"><Loader2 className="h-4 w-4 animate-spin" />{t('ui.shared.loading')}</div>
        : !activeId && <p className="p-8 text-center text-sm text-muted">{t('ui.shared.noAccess')}</p>}
      {activeId && access?.subject && <SharedTaskInput key={activeId + access.subject} onCapabilities={updateCapabilities} sessionId={activeId} subject={access.subject} initialContribute={access.capabilities?.[activeId]?.includes('contribute') ?? false} />}
    </ConversationPane></ViewerIdentityContext.Provider>
    <Toaster />
  </div>
  return <main className="flex h-dvh min-h-0 flex-col bg-surface text-ink" data-shared-session="">
    <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-line px-4 py-3">
      <div className="min-w-0 basis-full sm:basis-0 sm:flex-1"><h1 className="truncate text-base font-semibold">{t('ui.shared.title')}</h1>
        <p className="text-xs text-muted">{t(access?.read_only === false ? 'ui.shared.contributing' : 'ui.shared.readOnly')}</p></div>
      <Button size="sm" variant="ghost" aria-label={t('ui.shared.language')} onClick={() => setLocale(locale === 'zh-CN' ? 'en' : 'zh-CN')} icon={<Globe className="h-4 w-4" />}>{t('ui.shared.language')}</Button>
      <Button size="sm" variant="ghost" aria-label={t('ui.shared.theme')} onClick={() => useAppStore.getState().toggleTheme()} icon={<Moon className="h-4 w-4" />} />

    </header>
    {error && <div role="alert" className="border-b border-danger/20 bg-danger/[0.12] px-4 py-3 text-sm text-danger">{error}</div>}
    {busy ? <div role="status" className="flex flex-1 items-center justify-center gap-3 text-sm text-muted"><Loader2 className="h-4 w-4 animate-spin" />{t('ui.shared.loading')}</div>
      : <section className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto p-5">
        {access && !access.enabled ? <p className="max-w-md text-sm text-muted">{t('ui.shared.unavailable')}</p> : <form onSubmit={event => void signIn(event)} className="w-full max-w-md space-y-4 rounded-xl border border-line bg-raised p-6">
          <h2 className="text-xl font-semibold">{t('ui.shared.join')}</h2><p className="text-sm text-muted">{t('ui.shared.hint')}</p>
          <label className="block space-y-2 text-sm">{t('ui.shared.credential')}<SecretInput autoFocus={visible} autoComplete="off" spellCheck={false} maxLength={4096} value={token} onChange={event => setToken(event.target.value)} className="block h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:border-brand" /></label>
          <div className="flex flex-wrap gap-3"><Button type="submit" variant="primary" disabled={busy || !token.trim()}>{t('ui.shared.join')}</Button><Button variant="ghost" onClick={refresh}>{t('ui.shared.retry')}</Button></div>
        </form>}
      </section>}
    <Toaster />
  </main>
}
