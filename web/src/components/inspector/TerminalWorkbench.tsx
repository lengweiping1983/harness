import {loadTerminalEmulator} from '@/lib/terminalEmulator'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import {projectTerminalSession} from '@/lib/projectTerminalSession'
import {useWorkspaceTabs} from './WorkspaceTabs'
import {preferredTerminalType} from '@/lib/terminalTypePreference'
import { t, useLocale } from '@/i18n'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, CornerDownLeft, Globe, Loader2, Plus, Quote, RotateCw, Square, Terminal as TerminalIcon } from 'lucide-react'
import type { Terminal } from '@xterm/xterm'
import type { ResourceText, RuntimeTerminal, ToolBlockState } from '@/types'
import { api } from '@/lib/api'
import { localPreviewUrls } from '@/lib/localPreview'
import { terminalResizeQueue } from '@/lib/terminalResize'
import { terminalInputQueue, terminalInputDisabled } from '@/lib/terminalInput'
import { terminalCommands, terminalCommandText, terminalCommandBody, terminalChoice, rememberTerminalChoice, hiddenTerminalTabs, hideTerminalTab, showTerminalTab, terminalChoiceAfterClose, terminalSnapshot } from '@/lib/terminalCommands'
import { toolActivity } from '@/lib/toolActivity'
import { commandResult } from '@/lib/commandResults'
import { cn, formatDuration } from '@/lib/utils'
import { useAppStore } from '@/store/app'
import { useChat } from '@/store/chat'
import { Button } from '@/components/ui/primitives'
import { TerminalSetupPanel } from './TerminalSetupPanel'
import '@xterm/xterm/css/xterm.css'

export function TerminalWorkbench({ sessionId, active = true }: { sessionId: string | null; active?: boolean }) {
  useLocale()

  useEffect(() => {
    if (active) void loadTerminalEmulator().catch(() => { /* 实际挂载模拟器时呈现加载错误。 */ })
  }, [active])
  const cwd = useAppStore(state => state.workspaces.find(workspace => workspace.id === state.activeWorkspaceId)?.path ?? state.workspace?.root)
  const [projectSession, setProjectSession] = useState<{cwd: string | undefined; id: string} | null>(null)
  const [error, setError] = useState<ResourceText>('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (sessionId || !active) return
    let current = true
    setError('')
    void projectTerminalSession(cwd).then(id => {if (current) setProjectSession({cwd, id})}, error => {if (current) setError(failureText(error))})
    return () => {current = false}
  }, [sessionId, active, cwd, retry])
  const owner = sessionId ?? (projectSession?.cwd === cwd ? projectSession?.id : null)
  return owner ? <Workbench key={owner} sessionId={owner} headerSessionId={sessionId} active={active} /> : <div className="flex h-full flex-col items-center justify-center gap-3 p-4">
    {error ? <><p role="alert" className="text-xs text-danger">{noticeText(error)}</p><Button onClick={() => setRetry(value => value + 1)}>{t('ui.terminalSetup.retry')}</Button></> : <Loader2 role="status" aria-label={t('ui.components.inspector.TerminalWorkbench.7105a374bc75')} className="h-5 w-5 animate-spin text-muted"/>}
  </div>
}

function Workbench({ sessionId, headerSessionId, active }: { sessionId: string; headerSessionId: string | null; active: boolean }) {
  useLocale()

  const chat = useChat(sessionId)
  const commands = terminalCommands(chat.blocks)
  const [terminals, setTerminals] = useState<RuntimeTerminal[]>([])
  const [selected, setSelected] = useState(() => terminalChoice(sessionId))
  const [hidden, setHidden] = useState(() => hiddenTerminalTabs(sessionId))
  const [closing, setClosing] = useState<string[]>([])
  const closePending = useRef(new Set<string>())
  const [closeError, setCloseError] = useState<ResourceText | null>(null)
  const [closeRequest, setCloseRequest] = useState<string | null>(null)
  const closeCancel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (active && closeRequest) closeCancel.current?.querySelector<HTMLButtonElement>('button')?.focus()
  }, [active, closeRequest])
  const target = useAppStore(state => state.inspectorTerminalTarget)
  useEffect(() => {
    if (target?.sessionId === sessionId) {
      setHidden(showTerminalTab(sessionId, target.terminalId))
      setSelected(target.terminalId)
      useAppStore.setState({ inspectorTerminalTarget: null })
    }
  }, [sessionId, target])
  useEffect(() => { rememberTerminalChoice(sessionId, selected) }, [sessionId, selected])
  const [kind, setKind] = useState(preferredTerminalType)
  const [backends, setBackends] = useState<string[]>([])
  const [enabled, setEnabled] = useState(false)
  const [managedRuntime, setManagedRuntime] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState<ResourceText | null>(null)
  const [openError, setOpenError] = useState<ResourceText | null>(null)
  const revision = useRef(0)
  const alive = useRef(true)
  const update = useCallback((terminal: RuntimeTerminal) => {
    revision.current++
    setTerminals(rows => rows.some(row => row.sessionId === terminal.sessionId)
      ? rows.map(row => row.sessionId === terminal.sessionId ? terminalSnapshot(row, terminal) : row) : [...rows, terminal])
  }, [])
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      const before = revision.current
      const result = await api.terminals(sessionId, controller.signal)
      if (controller.signal.aborted) return
      if (result.ok) {
        if (before === revision.current) setTerminals(rows => result.data.terminals.map(row => terminalSnapshot(rows.find(previous => previous.sessionId === row.sessionId), row)))
        setKind(current => {const preferred = preferredTerminalType() || current; return result.data.backends.includes(preferred) ? preferred : result.data.default_type})
        setBackends(result.data.backends)
        setEnabled(result.data.enabled)
        setManagedRuntime(result.data.managed_runtime)
        setLoadError(null)
      } else setLoadError(result.copy ?? result.message)
      setLoading(false)
      timer = setTimeout(() => void poll(), 1500)
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [sessionId, active])
  const open = async () => {
    const preferred = preferredTerminalType()
    const selectedKind = backends.includes(preferred) ? preferred : kind
    if (busy || !enabled || !backends.includes(selectedKind)) return
    setBusy(true)
    setOpenError(null)
    const result = await api.openTerminal(sessionId, selectedKind)
    if (!alive.current) return
    if (result.ok) {
      update(result.data.terminal); setSelected(result.data.terminal.sessionId)
      if (result.data.audit_error != null) useAppStore.getState().toast({ tone: 'warn', title: {key: 'ui.audit.terminalActionIncomplete'} })
    } else setOpenError(result.copy ?? result.message)
    setBusy(false)
  }
  const visible = terminals.filter(row => !hidden.includes(row.sessionId))
  const currentTabs = useRef<string[]>([])
  currentTabs.current = ['commands', ...visible.map(row => row.sessionId)]
  const close = async (id: string, confirmed = false) => {
    const row = terminals.find(item => item.sessionId === id)
    if (!row || closePending.current.has(id)) return
    if (!confirmed && (row.status.kind !== 'exited' || row.control)) { setCloseRequest(id); return }
    setCloseRequest(null)
    closePending.current.add(id); setClosing(ids => [...ids, id]); setCloseError(null)
    try {
      if (row.status.kind !== 'exited' || row.control) {
        const result = await api.stopTerminal(sessionId, id)
        if (!result.ok) { if (alive.current) setCloseError({template: {key: 'ui.components.inspector.TerminalWorkbench.e19d2d690b52', params: {p0: id, p1: result.copy ?? result.message}}}); return }
        if (alive.current) update(result.data.terminal)
        if (result.data.outcome === 'cleanup-failed' || result.data.terminal.status.kind !== 'exited' || result.data.terminal.control) {
          if (alive.current) setCloseError({template: {key: 'ui.components.inspector.TerminalWorkbench.d6238429541a', params: {p0: id, p1: result.data.cleanupError || {key: 'ui.components.inspector.TerminalWorkbench.6aed5bc36fc4'}}}})
          return
        }
        if (result.data.audit_error != null) useAppStore.getState().toast({ tone: 'warn', title: {key: 'ui.audit.terminalStopIncomplete'} })
      }
      const ids = hideTerminalTab(sessionId, id)
      if (alive.current) {
        setHidden(ids)
        const tabs = currentTabs.current.filter(value => value === id || !ids.includes(value))
        setSelected(current => terminalChoiceAfterClose(tabs, current, id))
      }
    } finally {
      closePending.current.delete(id)
      if (alive.current) setClosing(ids => ids.filter(value => value !== id))
    }
  }
  const terminal = visible.find(row => row.sessionId === selected)
  const [visited, setVisited] = useState<string[]>([])
  useEffect(() => {
    if (terminal) setVisited(ids => ids.includes(terminal.sessionId) ? ids : [...ids, terminal.sessionId])
  }, [terminal?.sessionId])
  const entered = useRef(false)
  useEffect(() => {
    if (!active) {entered.current = false; return}
    if (entered.current || loading || loadError || !enabled || !kind) return
    entered.current = true
    const existing = visible.find(row => row.sessionId === selected && row.status.kind === 'running')
      ?? visible.find(row => row.status.kind === 'running')
    if (existing) setSelected(existing.sessionId)
    else void open()
  }, [active, loading, loadError, enabled, kind, selected, terminals, hidden])
  const terminalLabel = (row: RuntimeTerminal) => row.name || t('ui.terminal.tabLabel', {type: row.type, number: terminals.findIndex(item => item.sessionId === row.sessionId) + 1})
  useWorkspaceTabs('terminal', {sessionId: headerSessionId, selected, create: () => void open(), select: setSelected, close: id => void close(id),
    options: visible.map(row => ({value: row.sessionId, label: terminalLabel(row), title: row.cwd,
      closable: !closing.includes(row.sessionId), closeLabel: t('ui.components.inspector.TerminalWorkbench.c876345163b9', {p0: terminalLabel(row)}),
      icon: closing.includes(row.sessionId) ? <Loader2 className="h-3 w-3 animate-spin"/> : <TerminalIcon className="h-3 w-3"/>}))})
  return <section className="terminal-workbench" aria-label={t("ui.components.inspector.TerminalWorkbench.b31edc5504cf")}>
    {managedRuntime ? <TerminalSetupPanel active={active} enabled={enabled} onReady={() => void open()} /> : null}
    {loadError ? <p role="alert" className="px-3 py-2 text-xs text-danger">{noticeText(loadError)}</p> : null}
    {openError ? <p role="alert" className="px-3 py-2 text-xs text-danger">{noticeText(openError)}</p> : null}
    {closeError ? <p role="alert" className="px-3 py-2 text-xs text-danger">{noticeText(closeError)}</p> : null}
    {closeRequest && terminals.some(row => row.sessionId === closeRequest) && <div role="group" aria-label={t('ui.terminal.closeConfirmTitle')} className="m-3 space-y-2 rounded-lg border border-line bg-raised p-3">
      <p className="text-xs [overflow-wrap:anywhere]">{t('ui.terminal.closeConfirm', { name: terminalLabel(terminals.find(row => row.sessionId === closeRequest)!) })}</p>
      <div ref={closeCancel} className="flex flex-wrap gap-2"><Button size="xs" onClick={() => setCloseRequest(null)}>{t('ui.memory.cancelDelete')}</Button><Button size="xs" variant="danger" onClick={() => void close(closeRequest, true)}>{t('ui.terminal.confirmClose')}</Button></div>
    </div>}
    {!loading && selected !== 'commands' && !terminal ? <p role="status" className="px-3 py-2 text-xs text-warn">{t("ui.components.inspector.TerminalWorkbench.bf84077208bf")}</p> : null}
    <div id="terminal-workspace-content" role="tabpanel" aria-label={selected === 'commands' ? t("ui.components.inspector.TerminalWorkbench.e58b3b897b45") : t("ui.components.inspector.TerminalWorkbench.ce59564793be")} className="min-h-0 flex-1">
      {visible.filter(row => visited.includes(row.sessionId) || row.sessionId === terminal?.sessionId).map(row => <div key={row.sessionId} hidden={row.sessionId !== terminal?.sessionId} className="h-full min-h-0"><TerminalCanvas sessionId={sessionId} terminal={row} active={active && row.sessionId === terminal?.sessionId} onUpdate={update} /></div>)}
      {!terminal ? <div className="h-full overflow-y-auto px-3 py-4">
        {loading ? <p className="flex items-center gap-2 text-xs text-muted"><Loader2 className="h-3 w-3 animate-spin" />{t("ui.components.inspector.TerminalWorkbench.7105a374bc75")}</p> : !commands.length ? <div className="flex h-full items-center justify-center">
          {busy ? <Loader2 role="status" aria-label={t('ui.components.inspector.TerminalWorkbench.7105a374bc75')} className="h-5 w-5 animate-spin text-muted" /> : <Button disabled={!enabled || !kind} onClick={() => void open()}><Plus className="h-3.5 w-3.5" />{t('ui.components.inspector.TerminalWorkbench.f54692db0bb3')}</Button>}
        </div> : <div className="space-y-5">{commands.map(block => <CommandReceipt key={block.id} block={block} sessionId={sessionId} />)}</div>}
      </div> : null}
    </div>
  </section>
}

function PreviewLinks({ urls, sessionId }: { urls: string[]; sessionId: string }) {
  return urls.length ? <div className="flex flex-wrap gap-2 border-b border-line px-3 py-2">{urls.map(url => <button
    key={url} type="button" title={t('ui.terminal.preview', { url })} aria-label={t('ui.terminal.preview', { url })}
    onClick={() => useAppStore.getState().revealBrowser(sessionId, url)}
    className="flex min-w-0 max-w-full items-center gap-1.5 rounded-md bg-brand/10 px-2 py-1 text-xs text-brand hover:bg-brand/20">
    <Globe className="h-3.5 w-3.5 shrink-0" /><span className="truncate font-mono">{url}</span>
  </button>)}</div> : null
}

function terminalBufferText(term: Terminal) {
  const buffer = term.buffer.active
  let output = ''
  for (let index = Math.max(0, buffer.length - 200); index < buffer.length; index++) {
    const line = buffer.getLine(index)
    if (line) output += (line.isWrapped ? '' : '\n') + line.translateToString(true)
  }
  return output.trim()
}

function CommandReceipt({ block, sessionId }: { block: ToolBlockState; sessionId: string }) {
  useLocale()

  const text = terminalCommandText(block)
  const activity = toolActivity(block)
  const result = commandResult(block)
  const command = terminalCommandBody(block)
  const view = useRef<HTMLPreElement>(null)
  const pinned = useRef(true)
  const quote = useAppStore(s => s.quoteInto)
  const display = block.display && typeof block.display === 'object' ? block.display as Record<string, unknown> : {}
  useEffect(() => { if (pinned.current && view.current) view.current.scrollTop = view.current.scrollHeight }, [text])
  return <article data-command-id={block.callId} className="border-b border-line pb-4 last:border-0">
    <div className="mb-2 flex items-center justify-between gap-2 text-2xs text-muted">
      <span className="flex min-w-0 items-center gap-1.5">{block.status === 'running' ? <Loader2 className="h-3 w-3 animate-spin text-brand" /> : <span className={cn('h-1.5 w-1.5 rounded-full', block.status === 'error' ? 'bg-danger' : 'bg-faint')} />}{activity.label}</span>
      <span className="shrink-0 tabular-nums">{result.exitCode !== null
        ? block.durationMs !== undefined ? t('ui.terminal.exitDuration', {code: result.exitCode, duration: formatDuration(block.durationMs)}) : t('ui.terminal.exit', {code: result.exitCode})
        : block.durationMs !== undefined ? formatDuration(block.durationMs) : ''}</span>
    </div>
    <code className="block whitespace-pre-wrap break-words font-mono text-xs leading-5">$ {command.slice(0, 16000)}</code>
    {result.note ? <p className="mt-2 text-2xs leading-5 text-warn">{result.note}</p> : null}
    {text ? <pre ref={view} tabIndex={0} aria-label={t("ui.components.inspector.TerminalWorkbench.22bf87e68b18")} className="terminal-command-output mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-raised p-3 font-mono text-xs leading-5 text-muted" onScroll={event => { const node = event.currentTarget; pinned.current = node.scrollHeight - node.clientHeight - node.scrollTop < 24 }}>{text.slice(-32768)}</pre> : <p className="py-2 text-2xs text-faint">{block.status === 'running' ? t("ui.components.inspector.TerminalWorkbench.2c51e2cdf927") : t("ui.components.inspector.TerminalWorkbench.612d89af4169")}</p>}
    {text && (text.length > 32768 || block.progress && block.progress.stdoutDropped + block.progress.stderrDropped > 0) ? <p className="mt-1 text-2xs text-faint">{t("ui.components.inspector.TerminalWorkbench.cc2992be290c")}</p> : null}
    <PreviewLinks urls={localPreviewUrls(text)} sessionId={sessionId} />
    <div className="mt-2 flex gap-3">
      {text ? <button className="text-2xs text-brand hover:underline" onClick={() => quote(sessionId, t("ui.components.inspector.TerminalWorkbench.509d7f62ac5a", {p0: (command.slice(0, 4000)), p1: (text.slice(-12000))}))}>{t("ui.components.inspector.TerminalWorkbench.bc8eb562bb08")}</button> : null}
      {typeof display.job_id === 'string' ? <button className="text-2xs text-brand hover:underline" onClick={() => useAppStore.getState().revealJob(sessionId, display.job_id as string)}>{t("ui.components.inspector.TerminalWorkbench.69a9aa482d56")}</button> : null}
    </div>
  </article>
}

function TerminalCanvas({ sessionId, terminal, active, onUpdate }: { sessionId: string; terminal: RuntimeTerminal; active: boolean; onUpdate: (row: RuntimeTerminal) => void }) {
  const locale = useLocale()

  const host = useRef<HTMLDivElement>(null)
  const emulator = useRef<Terminal | null>(null)
  const resizer = useRef<ReturnType<typeof terminalResizeQueue> | null>(null)
  const fitTerminal = useRef<(() => void) | null>(null)
  const [resizeError, setResizeError] = useState<ResourceText | null>(null)
  const writer = useRef<ReturnType<typeof terminalInputQueue> | null>(null)
  const [error, setError] = useState<ResourceText | null>(null)
  const [inputError, setInputError] = useState<ResourceText | null>(null)
  const inputBlocked = useRef(false)
  const [pipeText, setPipeText] = useState('')
  const [previewUrls, setPreviewUrls] = useState<string[]>([])
  const pipeSending = useRef(false)
  const [stopping, setStopping] = useState(false)
  const [following, setFollowing] = useState(true)
  // 隐藏前的连接状态不能用于本次激活；先读取确认，再开放输入。
  const activation = useMemo(() => ({}), [active])
  const [verified, setVerified] = useState<object | null>(null)
  const connected = active && verified === activation
  const observation = useRef({active, activation, verified})
  observation.current = {active, activation, verified}
  const [ready, setReady] = useState(false)
  const [setupExpired, setSetupExpired] = useState(false)
  const cursor = useRef(0)
  const painting = useRef<Promise<void> | null>(null)
  const live = useRef(true)
  const ended = terminal.status.kind === 'exited'
  const pipe = terminal.transport !== 'pty'
  useEffect(() => {
    live.current = true
    const controller = new AbortController()
    setSetupExpired(false)
    const setupTimer = setTimeout(() => {if (!controller.signal.aborted) setSetupExpired(true)}, 10000)
    setReady(false)
    let observer: ResizeObserver | undefined
    let themeObserver: MutationObserver | undefined
    let disposeInput: { dispose(): void } | undefined
    const queue = terminalInputQueue(async text => {
      const result = await api.terminalInput(sessionId, terminal.sessionId, text)
      if (!result.ok) return {template: {key: 'ui.components.inspector.TerminalWorkbench.403e9e4e38b1', params: {p0: result.copy ?? result.message}}}
      if (result.data.audit_error != null && live.current) useAppStore.getState().toast({ tone: 'warn', title: {key: 'ui.audit.terminalActionIncomplete'} })
      return null
    }, message => { inputBlocked.current = true; if (!controller.signal.aborted) setInputError(message) })
    const sizes = terminalResizeQueue(async size => {
      const result = await api.resizeTerminal(sessionId, terminal.sessionId, size.cols, size.rows)
      return result.ok ? null : result.copy ?? result.message
    }, message => { if (!controller.signal.aborted) setResizeError(message) })
    sizes.setActive(observation.current.active && !ended && !pipe)
    resizer.current = sizes
    writer.current = queue
    const setup = async () => {
      const [{ Terminal: Xterm }, { FitAddon }] = await loadTerminalEmulator()
      if (controller.signal.aborted || !host.current) return
      const term = new Xterm({ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', fontSize: 13, lineHeight: 1.45, cursorBlink: true, scrollback: 5000, allowProposedApi: false, disableStdin: true, convertEol: pipe })
      const fit = new FitAddon()
      term.loadAddon(fit); term.open(host.current); emulator.current = term
      if (pipe && term.textarea) { term.textarea.hidden = true; term.textarea.tabIndex = -1; term.textarea.setAttribute('aria-hidden', 'true') }
      term.textarea?.setAttribute('aria-label', t("ui.components.inspector.TerminalWorkbench.7539edf17e87"))
      const theme = () => {
        const style = getComputedStyle(document.documentElement)
        term.options.fontFamily = style.getPropertyValue('--font-mono').trim()
        term.options.fontSize = parseFloat(style.fontSize) * 0.8125
        const color = (name: string) => `rgb(${style.getPropertyValue('--c-' + name).trim().split(/\s+/).join(',')})`
        term.options.theme = { background: color('surface'), foreground: color('ink'), cursor: color('brand'), selectionBackground: color('line-strong') }
      }
      theme(); themeObserver = new MutationObserver(() => { theme(); resize() }); themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style'] })
      const resize = () => {
        if (controller.signal.aborted || !host.current?.clientWidth || !host.current.clientHeight) return
        fit.fit()
        if (!pipe) sizes.resize(term.cols, term.rows)
      }
      fitTerminal.current = resize
      observer = new ResizeObserver(resize); observer.observe(host.current); resize()
      // Native terminal keys must not trigger the chat's global Escape/Tab logic.
      const keys = (event: KeyboardEvent) => { if (!event.metaKey && !event.shiftKey && !(event.ctrlKey && event.code === 'Backquote')) event.stopPropagation() }
      host.current.addEventListener('keydown', keys)
      disposeInput = { dispose: () => { host.current?.removeEventListener('keydown', keys) } }
      term.onData(text => {
        const state = observation.current
        if (!pipe && state.active && state.verified === state.activation && !term.options.disableStdin) queue.push(text)
      })
      term.onScroll(() => setFollowing(term.buffer.active.viewportY >= term.buffer.active.baseY))
      setReady(true)
      setSetupExpired(false)
      clearTimeout(setupTimer)
    }
    void setup().catch(cause => { if (!controller.signal.aborted) setError(failureText(cause)) }).finally(() => clearTimeout(setupTimer))
    return () => {
      live.current = false; controller.abort()
      clearTimeout(setupTimer)
      observer?.disconnect(); themeObserver?.disconnect(); disposeInput?.dispose(); queue.dispose(); sizes.dispose()
      resizer.current = null; fitTerminal.current = null
      writer.current = null; emulator.current?.dispose(); emulator.current = null
    }
  }, [sessionId, terminal.sessionId, pipe])
  useEffect(() => {
    resizer.current?.setActive(active && !ended && !pipe)
    if (active) fitTerminal.current?.()
  }, [active, ended, pipe, ready])
  useEffect(() => {
    const term = emulator.current
    if (!active || !ready || !term) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      if (painting.current) await painting.current
      if (controller.signal.aborted) return
      const result = await api.terminalStream(sessionId, terminal.sessionId, cursor.current, controller.signal)
      if (controller.signal.aborted) return
      if (!result.ok) {
        setVerified(null); setError(result.copy ?? result.message)
        term.options.disableStdin = true
        timer = setTimeout(() => void poll(), 1500)
        return
      }
      setVerified(activation); setError(result.data.terminal.output_error ? {key: result.data.terminal.output_error.key, params: {error: String(result.data.terminal.output_error.params.error)}} : null); onUpdate(result.data.terminal)
      term.options.disableStdin = terminalInputDisabled({ pipe, connected: true, ended: result.data.terminal.status.kind === 'exited', blocked: inputBlocked.current, controlling: Boolean(result.data.terminal.control || result.data.terminal.output_error) })
      if (result.data.reset) term.reset()
      if (result.data.text) {
        // Returning to the tab must wait for any previous write to advance its cursor.
        const paint = new Promise<void>(resolve => term.write(result.data.text, () => { cursor.current = result.data.cursor; resolve() }))
        painting.current = paint
        await paint
        if (painting.current === paint) painting.current = null
        if (!controller.signal.aborted) {
          const urls = localPreviewUrls(terminalBufferText(term))
          setPreviewUrls(current => current.join('\n') === urls.join('\n') ? current : urls)
        }
      } else cursor.current = result.data.cursor
      if (controller.signal.aborted) return
      if (result.data.reset) setError({key: 'ui.components.inspector.TerminalWorkbench.12ebe25291c7'})
      // Drain retained output after process exit before stopping observation.
      if (cursor.current < result.data.end || (!result.data.terminal.output_error && (result.data.terminal.status.kind === 'running' || !result.data.eof))) timer = setTimeout(() => void poll(), result.data.text ? 16 : 150)
    }
    void poll()
    return () => { controller.abort(); clearTimeout(timer); term.options.disableStdin = true }
  }, [sessionId, terminal.sessionId, pipe, onUpdate, active, activation, ready])
  useEffect(() => {
    emulator.current?.textarea?.setAttribute('aria-label', t('ui.components.inspector.TerminalWorkbench.7539edf17e87'))
  }, [locale, ready])
  const inputDisabled = terminalInputDisabled({ pipe: false, connected, ended, blocked: Boolean(inputError), controlling: Boolean(terminal.control || terminal.output_error) })
  useEffect(() => { if (emulator.current) emulator.current.options.disableStdin = pipe || inputDisabled }, [pipe, inputDisabled, ready])
  const stop = async () => {
    setStopping(true)
    const result = await api.stopTerminal(sessionId, terminal.sessionId)
    if (!live.current) return
    if (result.ok) { onUpdate(result.data.terminal); setError(result.data.audit_error != null ? {key: 'ui.audit.terminalStopIncomplete'} : result.data.cleanupError ?? null) }
    else setError(result.copy ?? result.message)
    setStopping(false)
  }
  const quote = () => {
    const term = emulator.current
    if (!term) return
    const output = term.getSelection() || terminalBufferText(term)
    useAppStore.getState().quoteInto(sessionId, t("ui.components.inspector.TerminalWorkbench.a0c3eb115671", {p0: (terminal.sessionId), p1: (terminal.cwd), p2: (output.slice(-12000))}))
  }
  const sendPipe = async () => {
    if (!pipeText || inputDisabled || pipeSending.current) return
    pipeSending.current = true
    const text = pipeText; setPipeText('')
    const result = await api.terminalInput(sessionId, terminal.sessionId, text, true)
    pipeSending.current = false
    if (!live.current) return
    if (!result.ok) { setInputError(result.copy ?? result.message); setPipeText(current => current || text) }
    else if (result.data.audit_error != null) setError({key: 'ui.audit.terminalActionIncomplete'})
  }
  return <div className="terminal-canvas flex h-full min-h-0 flex-col" data-terminal-id={terminal.sessionId}>
    {setupExpired && !ready ? <p role="alert" className="px-3 py-2 text-xs text-danger">{t('ui.deferred.panel.failed', {name: t('ui.components.inspector.Inspector.e2a76ef1f12e')})}<button type="button" className="ml-2 inline-flex items-center gap-1 underline" onClick={() => window.location.reload()}><RotateCw aria-hidden className="h-3 w-3" />{t('ui.deferred.reload')}</button></p> : null}
    <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2 text-2xs text-muted">
      <span className={cn('h-1.5 w-1.5 rounded-full', ended ? 'bg-faint' : connected ? 'bg-brand' : 'bg-warn')} />
      <span>{ended ? terminal.status.exit_code != null ? t('ui.terminal.exitedCode', {code: terminal.status.exit_code}) : t('ui.terminal.exitedSignal', {signal: terminal.status.signal}) : connected ? t("ui.components.inspector.TerminalWorkbench.5be0323e8adc") : t("ui.components.inspector.TerminalWorkbench.694186911ae3")}</span>
      <span>{t(pipe ? 'ui.terminal.mode.command' : 'ui.terminal.mode.interactive')}</span>
      <span className="min-w-0 flex-1 truncate font-mono" title={terminal.cwd}>{terminal.cwd}</span>
      {!pipe ? <button aria-label={t("ui.components.inspector.TerminalWorkbench.c0b4076eb68f")} disabled={inputDisabled} onClick={() => writer.current?.push('\x03')} className="rounded px-1 py-1 hover:bg-raised disabled:opacity-40">{t("ui.components.inspector.TerminalWorkbench.f9d19345a067")}</button> : null}
      <button aria-label={t("ui.components.inspector.TerminalWorkbench.74045d161c09")} title={t("ui.components.inspector.TerminalWorkbench.4382e44fff9e")} onClick={quote} className="rounded p-1 hover:bg-raised"><Quote className="h-3.5 w-3.5" /></button>
      <button aria-label={t("ui.components.inspector.TerminalWorkbench.2993cb23dc3e")} title={t("ui.components.inspector.TerminalWorkbench.022c316b04c6")} disabled={stopping || ended && !terminal.control} onClick={() => void stop()} className="rounded p-1 text-danger hover:bg-raised disabled:opacity-40">{stopping ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Square className="h-3 w-3" />}</button>
    </div>
    <PreviewLinks urls={previewUrls} sessionId={sessionId} />
    {terminal.encoding_errors?.map((issue, index) => <div key={index} role="alert" className="border-b border-line px-3 py-2 text-xs text-warn">{t('ui.encoding.failure', {...issue})}</div>)}
    {error || resizeError || inputError || terminal.control?.error ? <div role="alert" className="border-b border-line px-3 py-2 text-xs text-warn">{noticeText(inputError || terminal.control?.error || resizeError || error || '')}{inputError ? <button className="ml-2 text-brand underline" onClick={() => { if (!writer.current?.resume()) return; inputBlocked.current = false; setInputError(null); emulator.current?.focus() }}>{t("ui.components.inspector.TerminalWorkbench.3b63c5cbd2ed")}</button> : null}</div> : null}
    <div className="relative min-h-0 flex-1">
      <div ref={host} role={pipe ? 'log' : undefined} aria-label={pipe ? t('ui.terminal.output') : undefined} className="terminal-emulator h-full overflow-hidden px-3 py-2" />
      {!following ? <button className="absolute bottom-4 right-4 flex items-center gap-1 rounded-md border border-line bg-surface px-2 py-1 text-xs shadow-card" onClick={() => { emulator.current?.scrollToBottom(); setFollowing(true) }}><ArrowDown className="h-3 w-3" />{t("ui.components.inspector.TerminalWorkbench.1c59875d3a15")}</button> : null}
    </div>
    {pipe ? <form className="flex gap-2 border-t border-line px-3 py-2" onSubmit={event => { event.preventDefault(); void sendPipe() }}><input aria-label={t("ui.components.inspector.TerminalWorkbench.a065d91d9713")} value={pipeText} onChange={event => setPipeText(event.target.value)} disabled={inputDisabled} className="min-w-0 flex-1 bg-transparent font-mono text-xs" placeholder={t("ui.components.inspector.TerminalWorkbench.6977b7811f1b")} /><Button size="sm" disabled={inputDisabled} type="submit"><CornerDownLeft className="h-3 w-3" />{t("ui.components.inspector.TerminalWorkbench.32c8d373afe0")}</Button></form> : null}
    <footer className="border-t border-line px-3 py-2 text-2xs text-faint">{pipe ? t("ui.components.inspector.TerminalWorkbench.804dda88359b") : t("ui.components.inspector.TerminalWorkbench.5887c4c7039b")}</footer>
  </div>
}
