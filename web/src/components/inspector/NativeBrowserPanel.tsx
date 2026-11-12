import type {ResourceCopy, ResourceText} from '@/types'
import {noticeText} from '@/lib/noticeText'
import {failureText, ResourceError} from '@/lib/resourceError'
import {BrowserActivityBar} from './BrowserActivityBar'
import {browserSessionMatches} from '@/lib/nativeBrowserState'
import {useWorkspaceTabs} from './WorkspaceTabs'
import {browserAddress} from '@/lib/browser-address'
import {Undo2, ArrowLeft, ArrowRight, ArrowUp, Download, FolderOpen, Pause, Play, Globe, Hand, Loader2, RotateCw, Search, ChevronUp, ChevronDown, ShieldCheck, X} from 'lucide-react'
import {BrowserDialogPanel} from './BrowserDialogPanel'
import type {BrowserDialog} from '@/lib/browser'
import {Button, IconButton} from '@/components/ui/primitives'
import {t, useLocale} from '@/i18n'
import {formatBytes} from '@/lib/utils'
import {useEffect, useRef, useState} from 'react'

type PermissionState = {origin: string | null; grants: string[]; requests: {id: string; origin: string; capabilities: string[]}[]}
type DownloadState = {error?: ResourceText; reason?: 'limit'; id: string; filename: string; path: string; source: string; received: number; total: number; state: string; paused: boolean; resumable: boolean; done: boolean}
type State = {session_id: string; can_reopen?: boolean; dialog?: (Omit<BrowserDialog, 'page_id'> & {default_value?: string; responding?: boolean}) | null;find?: {open:boolean; text:string; matches:number; ordinal:number};info?: {chromium: string; electron: string; downloads: string};downloads?: DownloadState[];permissions?: PermissionState;owner?: 'user' | 'agent'; active_tab: string | null; tabs: {id: string; target_id?: string; zoom?: number; url: string; title: string; loading: boolean; issue?: 'load-failed' | 'crashed' | 'unresponsive' | 'closed' | null; can_go_back: boolean; can_go_forward: boolean}[]}
type Desktop = {onFocusFind: (listener: () => void) => () => void;onBrowserError: (listener: (failure: {session_id: string; message: string; code?: string; copy?: ResourceCopy}) => void) => () => void;browser: (action: string, args?: Record<string, unknown>) => Promise<State | {failure: {code: string; copy: ResourceCopy}}>; onBrowserState: (listener: (state: State) => void) => () => void; onFocusAddress: (listener: () => void) => () => void}
export const desktopBrowser = () => (window as unknown as {harnessDesktop?: Desktop}).harnessDesktop

export function NativeBrowserPanel({active = true, sessionId}: {active?: boolean; sessionId: string}) {
  useLocale()
  const findInput = useRef<HTMLInputElement>(null)
  const [findText, setFindText] = useState('')
  const addressInput = useRef<HTMLInputElement>(null)
  const addressDrafts = useRef(new Map<string, string>())
  const host = useRef<HTMLDivElement>(null)
  const [state, setState] = useState<State>({session_id: sessionId, active_tab: null, tabs: []})
  const [address, setAddress] = useState('')
  const [dialogPending, setDialogPending] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const tab = state.tabs.find(tab => tab.id === state.active_tab)
  async function request(action: string, args: Record<string, unknown>) {
    const result = await desktopBrowser()!.browser(action, args)
    if ('failure' in result) throw Object.assign(new ResourceError(result.failure.copy), {code: result.failure.code})
    return result
  }
  async function invoke(action: string, args = {}) {
    try {const next = await request(action, {session_id: sessionId, ...args}); if (mounted.current && browserSessionMatches(next, sessionId)) {setState(next); setError('')}} catch (error) {if (mounted.current) setError(failureText(error))}
  }
  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    const unsubscribe = desktopBrowser()!.onBrowserState(next => {if (browserSessionMatches(next, sessionId)) setState(next)})
    void invoke('bind', {session_id: sessionId})
    return () => {mounted.current = false; unsubscribe()}
  }, [sessionId])
  useEffect(() => {
    setAddress((tab?.id && addressDrafts.current.get(tab.id)) ?? (tab?.url === 'about:blank' ? '' : tab?.url ?? ''))
    const ids = new Set(state.tabs.map(tab => tab.id))
    for (const id of addressDrafts.current.keys()) if (!ids.has(id)) addressDrafts.current.delete(id)
  }, [tab?.url, tab?.id, state.tabs])
  useEffect(() => {setFindText(state.find?.text || '')}, [tab?.id, state.find?.open])
  useEffect(() => desktopBrowser()!.onFocusFind(() => {requestAnimationFrame(() => {findInput.current?.focus(); findInput.current?.select()})}), [])
  useEffect(() => desktopBrowser()!.onBrowserError(failure => {
    if (mounted.current && failure?.session_id === sessionId && typeof failure.message === 'string') setError(failure.copy ?? failure.message)
  }), [sessionId])
  useEffect(() => desktopBrowser()!.onFocusAddress(() => {addressInput.current?.focus({preventScroll:true}); addressInput.current?.select()}), [])
  useEffect(() => {
    if (!active) return
    const element = host.current!
    const layout = () => {
      const rect = element.getBoundingClientRect()
      void desktopBrowser()!.browser('layout', {session_id: sessionId, visible: active && !document.hidden && !document.querySelector('[aria-modal="true"]') && !element.closest('[aria-hidden="true"]') && rect.width > 0 && rect.height > 0, bounds: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}}).catch(error => setError(failureText(error)))
    }
    const observer = new ResizeObserver(layout); observer.observe(element)
    const mutations = new MutationObserver(layout); mutations.observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['aria-hidden', 'aria-modal']})
    window.addEventListener('scroll', layout, true); window.addEventListener('resize', layout); document.addEventListener('visibilitychange', layout)
    layout()
    return () => {void desktopBrowser()!.browser('keyboard_context', {session_id: sessionId, active:false}).catch(() => {}); observer.disconnect(); mutations.disconnect(); window.removeEventListener('scroll', layout, true); window.removeEventListener('resize', layout); document.removeEventListener('visibilitychange', layout); void desktopBrowser()!.browser('layout', {session_id: sessionId, visible: false, bounds: {x: 0, y: 0, width: 0, height: 0}}).catch(() => {})}
  }, [active, sessionId])
  useWorkspaceTabs('browser', {sessionId, selected: state.active_tab, create: () => void invoke('new_tab'),
    select: id => void invoke('select_tab', {id}), close: id => void invoke('close_tab', {id}),
    options: state.tabs.map(tab => ({value: tab.id, label: tab.title || t('browser.desktop.newTab'), title: tab.url, iconKey: tab.loading ? 'loading' : 'ready', icon: tab.loading ? <Loader2 className="h-3 w-3 animate-spin"/> : <Globe className="h-3 w-3"/>, closeLabel: t('browser.desktop.closeTab')}))})
  return <div className="flex h-full min-h-0 flex-col" onFocusCapture={() => {void desktopBrowser()!.browser('keyboard_context', {session_id: sessionId, active:true})}} onBlurCapture={event => {if (!event.currentTarget.contains(event.relatedTarget as Node | null)) void desktopBrowser()!.browser('keyboard_context', {session_id: sessionId, active:false})}}>
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-surface">
      <form className="flex min-w-0 items-center gap-1 border-b border-line p-2" onSubmit={event => {event.preventDefault(); const url = browserAddress(address); if (url) {if (tab) addressDrafts.current.delete(tab.id); void invoke('navigate', {url})} else setError({key:'browser.desktop.invalidAddress'})}}>
        <IconButton label={t('browser.desktop.back')} disabled={!tab?.can_go_back} onClick={() => void invoke('back')}><ArrowLeft className="h-3.5 w-3.5"/></IconButton>
        <IconButton label={t('browser.desktop.forward')} disabled={!tab?.can_go_forward} onClick={() => void invoke('forward')}><ArrowRight className="h-3.5 w-3.5"/></IconButton>
        <IconButton label={tab?.loading ? t('browser.desktop.stop') : t('browser.desktop.reload')} disabled={!tab} onClick={() => void invoke(tab?.loading ? 'stop' : 'reload')}>{tab?.loading ? <X className="h-3.5 w-3.5"/> : <RotateCw className="h-3.5 w-3.5"/>}</IconButton>
        {state.can_reopen && <IconButton label={t('browser.desktop.reopenTab')} onClick={() => void invoke('reopen_tab')}><Undo2 className="h-3.5 w-3.5"/></IconButton>}
        <input ref={addressInput} className="min-w-0 flex-1 rounded-md border border-line bg-canvas px-2 py-1.5 text-xs outline-none focus:border-brand" aria-label={t('browser.desktop.address')} placeholder={t('browser.desktop.placeholder')} value={address} onChange={event => {if (tab) addressDrafts.current.set(tab.id, event.target.value); setAddress(event.target.value)}} onKeyDown={event => {if (event.key === 'Escape' && !event.nativeEvent.isComposing) {event.preventDefault(); if (tab) addressDrafts.current.delete(tab.id); setAddress(tab?.url === 'about:blank' ? '' : tab?.url ?? ''); event.currentTarget.select()}}}/>
        {tab && Math.abs((tab.zoom ?? 1) - 1) > .01 && <button type="button" aria-label={t('browser.desktop.zoomReset')} title={t('browser.desktop.zoomReset')} onClick={() => void invoke('zoom', {direction:0})} className="rounded px-1.5 py-1 text-xs tabular-nums text-muted hover:bg-raised hover:text-ink">{Math.round((tab.zoom ?? 1) * 100)}%</button>}
        <IconButton label={t('browser.desktop.find.open')} disabled={!tab} onClick={() => void invoke('find_open')}><Search className="h-3.5 w-3.5"/></IconButton>
        <button type="submit" aria-label={t('browser.desktop.open')} disabled={!address.trim()} className="rounded p-1.5 text-muted hover:text-ink disabled:opacity-30"><ArrowUp className="h-3.5 w-3.5"/></button>
        <span className="ml-1 hidden shrink-0 items-center gap-1 text-xs text-muted xl:flex" role="status"><span className="h-1.5 w-1.5 rounded-full bg-ok"/>{t(state.owner === 'agent' ? 'browser.desktop.agentControl' : 'browser.desktop.userControl')}</span>
        <Button size="xs" variant={state.owner === 'agent' ? 'secondary' : 'primary'} title={t(state.owner === 'agent' ? 'browser.desktop.agentControl' : 'browser.desktop.userControl')} onClick={() => void invoke('control', {owner: state.owner === 'agent' ? 'user' : 'agent'})}><Hand className="h-3 w-3"/>{t(state.owner === 'agent' ? 'browser.desktop.takeControl' : 'browser.desktop.giveControl')}</Button>
      </form>
      {tab?.target_id && <BrowserActivityBar key={tab.target_id} sessionId={sessionId} pageId={tab.target_id} active={active}/>}
      {state.dialog && <BrowserDialogPanel key={state.dialog.id} dialog={{...state.dialog, page_id:state.active_tab || ''}} control pending={dialogPending || !!state.dialog.responding} onRespond={(accept, text) => {setDialogPending(true); void invoke('dialog_response', {id:state.dialog!.id, accept, prompt_text:text}).finally(() => setDialogPending(false))}}/>}
      {state.find?.open && <div role="search" aria-label={t('browser.desktop.find.open')} className="flex items-center gap-1 border-b border-line bg-raised px-2 py-1.5">
        <input maxLength={1000} ref={findInput} aria-label={t('browser.desktop.find.text')} placeholder={t('browser.desktop.find.text')} value={findText} onChange={event => {setFindText(event.target.value); void invoke('find', {text:event.target.value})}} onKeyDown={event => {if (event.key === 'Enter' && !event.nativeEvent.isComposing) {event.preventDefault(); void invoke('find_next', {forward:!event.shiftKey})}}} className="min-w-0 flex-1 rounded-md border border-line bg-canvas px-2 py-1 text-xs outline-none focus:border-brand"/>
        <span role="status" aria-live="polite" className="shrink-0 text-xs tabular-nums text-muted">{state.find.ordinal}/{state.find.matches}</span>
        <IconButton label={t('browser.desktop.find.previous')} disabled={!findText || !state.find.matches} onClick={() => void invoke('find_next', {forward:false})}><ChevronUp className="h-3.5 w-3.5"/></IconButton>
        <IconButton label={t('browser.desktop.find.next')} disabled={!findText || !state.find.matches} onClick={() => void invoke('find_next')}><ChevronDown className="h-3.5 w-3.5"/></IconButton>
        <IconButton label={t('browser.desktop.find.close')} onClick={() => void invoke('find_close')}><X className="h-3.5 w-3.5"/></IconButton>
      </div>}
      {!!state.downloads?.length && <details className="max-h-52 shrink-0 overflow-y-auto border-b border-line px-3 py-2 text-xs" open>
        <summary className="cursor-pointer text-muted">{t('browser.desktop.downloads.title', {count:state.downloads.length})}</summary>
        <div className="mt-2 space-y-2">{state.downloads.map(download => <div key={download.id} className="rounded-lg border border-line p-2" role="group" aria-label={download.filename}>
          <div className="flex items-center gap-2"><Download className="h-3.5 w-3.5 shrink-0 text-muted"/><span className="min-w-0 flex-1 truncate font-medium" title={download.path}>{download.filename}</span>
            {download.done && download.state === 'completed' ? <IconButton label={t('browser.desktop.downloads.show')} onClick={() => void invoke('download_action', {id:download.id, action:'show'})}><FolderOpen className="h-3.5 w-3.5"/></IconButton> : !download.done && <>
              {(download.paused || download.resumable) ? <IconButton label={t('browser.desktop.downloads.resume')} onClick={() => void invoke('download_action', {id:download.id, action:'resume'})}><Play className="h-3.5 w-3.5"/></IconButton> : download.state === 'progressing' && <IconButton label={t('browser.desktop.downloads.pause')} onClick={() => void invoke('download_action', {id:download.id, action:'pause'})}><Pause className="h-3.5 w-3.5"/></IconButton>}
              <IconButton label={t('browser.desktop.downloads.cancel')} onClick={() => void invoke('download_action', {id:download.id, action:'cancel'})}><X className="h-3.5 w-3.5"/></IconButton>
            </>}
          </div>
          <p className="mt-1 text-faint">{t('browser.desktop.downloads.' + (download.paused ? 'paused' : download.state))} · {formatBytes(download.received)}{download.total > 0 && ' / ' + formatBytes(download.total)}</p>
          {download.reason === 'limit' && <p className="mt-1 text-warn">{t('browser.desktop.downloads.limit')}</p>}
          {download.error && <p role="alert" className="mt-1 text-danger">{noticeText(download.error)}</p>}
          {!download.done && <progress className="mt-1 h-1 w-full accent-brand" aria-label={download.filename} max={download.total || undefined} value={download.total ? download.received : undefined}/>}
        </div>)}</div>
      </details>}
      {state.permissions?.requests.map(request => <div key={request.id} role="region" aria-label={t('browser.desktop.permissions.request')} className="space-y-2 border-b border-line bg-raised p-3">
        <div className="flex items-center gap-2 text-xs font-medium"><ShieldCheck className="h-4 w-4 shrink-0 text-brand"/><span className="min-w-0 break-all">{request.origin}</span></div>
        <p className="text-xs text-muted">{t('browser.desktop.permissions.wants', {permissions:request.capabilities.map(name => t('browser.desktop.permissions.' + name)).join(t('ui.common.list_separator'))})}</p>
        <div className="flex gap-2"><Button size="xs" variant="primary" onClick={() => void invoke('permission_answer', {id:request.id, allow:true})}>{t('browser.desktop.permissions.allow')}</Button><Button size="xs" onClick={() => void invoke('permission_answer', {id:request.id, allow:false})}>{t('browser.desktop.permissions.deny')}</Button></div>
      </div>)}
      {!!state.permissions?.grants.length && <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-1 text-xs"><span className="min-w-0 text-muted">{state.permissions.grants.map(name => t('browser.desktop.permissions.' + name)).join(t('ui.common.list_separator'))}</span><Button size="xs" variant="ghost" onClick={() => void invoke('permission_reset')}>{t('browser.desktop.permissions.reset')}</Button></div>}
      {error && <div role="alert" className="border-b border-line px-3 py-2 text-xs text-danger">{noticeText(error)}</div>}
      <div ref={host} id="native-browser-view" role="tabpanel" aria-label={t('browser.desktop.page')} className="min-h-0 flex-1">{tab?.issue && <div role="status" className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center"><Globe className="h-8 w-8 text-faint"/><h3 className="text-sm font-medium">{t('browser.desktop.failure.' + tab.issue)}</h3><p className="max-w-sm text-xs leading-relaxed text-muted">{t('browser.desktop.retryHint')}</p><Button size="sm" variant="secondary" onClick={() => void invoke('reload')}><RotateCw className="h-3.5 w-3.5"/>{t('browser.desktop.retry')}</Button></div>}{!tab && <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center text-sm text-muted"><Globe className="h-8 w-8 text-faint"/><span>{t('browser.desktop.empty')}</span><span className="text-xs text-faint">{t('browser.desktop.shortcut')}</span></div>}</div>
    </div>
  </div>
}
