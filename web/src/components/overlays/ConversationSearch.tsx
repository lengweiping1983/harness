import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale, getLocale } from '@/i18n'
import { useEffect, useId, useRef, useState } from 'react'
import { ArrowLeft, Search, X } from 'lucide-react'
import { api } from '@/lib/api'
import {validSearchResult} from '@/lib/conversationSearch'
import { validConversationPart } from '@/lib/turnCompletion'
import { sessionDirectory } from '@/lib/workspaces'
import { useActiveSession, useAppStore } from '@/store/app'
import type { ConversationMessagePart, ConversationSearchHit, ConversationSearchResult } from '@/types'
import { Button, CopyButton, IconButton, Overlay, RefreshButton, ShowMoreControl } from '@/components/ui/primitives'
import { Markdown } from '@/components/markdown/Markdown'

export function ConversationSearch() {
  useLocale()

  const open = useAppStore(s => s.conversationSearchOpen)
  const session = useActiveSession()
  if (!open || !session) return null
  return <SearchDialog key={session.id} sessionId={session.id} title={session.title || t("ui.components.overlays.ConversationSearch.159dfc134c9b")} root={sessionDirectory(session)} />
}

function timestamp(value: number) {
  return new Date(value).toLocaleString(getLocale(), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function SearchDialog({ sessionId, title, root }: { sessionId: string; title: string; root: string | null }) {
  useLocale()

  const close = () => useAppStore.getState().setConversationSearchOpen(false)
  const id = useId()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ConversationSearchResult | null>(null)
  const [before, setBefore] = useState<number | undefined>()
  const pinned = useRef<number | undefined>()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const [retry, setRetry] = useState(0)
  const [selection, setSelection] = useState<{ hit: ConversationSearchHit; start: number } | null>(null)
  const [message, setMessage] = useState<ConversationMessagePart | null>(null)
  const [reading, setReading] = useState(false)
  const [readError, setReadError] = useState<ResourceText>('')
  const [readRetry, setReadRetry] = useState(0)
  const [sourceView, setSourceView] = useState(false)
  const reader = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const normalized = query.trim()

  useEffect(() => {
    if (!normalized) { setLoading(false); return }
    const controller = new AbortController()
    let alive = true
    setLoading(true); setError('')
    const timer = setTimeout(() => {
      const through = pinned.current
      void api.searchConversation(sessionId, normalized, { signal: controller.signal, through, before }).then(result => {
        if (!alive) return
        if (!result.ok) { setError(result.copy ?? result.message); return }
        if (!validSearchResult(result.data, sessionId, normalized, through, before, before === undefined ? undefined : results?.total)) {
          setError({key: 'ui.http.invalidResponse'}); return
        }
        pinned.current = result.data.through
        setResults(previous => before === undefined ? result.data : { ...result.data, results: [...(previous?.results ?? []), ...result.data.results] })
      }).finally(() => { if (alive) setLoading(false) })
    }, before === undefined ? 250 : 0)
    return () => { alive = false; clearTimeout(timer); controller.abort() }
  }, [sessionId, normalized, before, retry])

  useEffect(() => {
    if (!selection) return
    const controller = new AbortController()
    let alive = true
    setMessage(null); setReading(true); setReadError('')
    void api.readConversationMessage(sessionId, selection.hit.source_seq, selection.start, controller.signal).then(result => {
      if (!alive) return
      if (result.ok) {
        if (!validConversationPart(result.data, sessionId, selection.hit.source_seq, selection.start, selection.hit.role)) { setReadError({key: 'ui.http.invalidResponse'}); return }
        setMessage(result.data); reader.current?.focus({ preventScroll: true }); reader.current?.scrollTo({ top: 0 }) }
      else setReadError(result.copy ?? result.message)
    }).finally(() => { if (alive) setReading(false) })
    return () => { alive = false; controller.abort() }
  }, [sessionId, selection, readRetry])

  useEffect(() => {
    if (!message) return
    const frame = requestAnimationFrame(() => {
      const match = reader.current?.querySelector<HTMLElement>('[data-search-match]')
      if (sourceView && match) match.scrollIntoView({ block: 'center' })
      else reader.current?.scrollTo({ top: 0 })
    })
    return () => cancelAnimationFrame(frame)
  }, [message, sourceView])

  const selectHit = (hit: ConversationSearchHit) => {
    setSourceView(hit.start_char > 0)
    setSelection({ hit, start: hit.start_char })
  }
  const matchAt = message && selection ? message.text.indexOf(selection.hit.match) : -1

  const update = (text: string) => {
    setQuery(text)
    if (text.trim() === normalized) return
    setResults(null); setBefore(undefined); pinned.current = undefined
    setSelection(null); setError('')
  }
  const refresh = () => { pinned.current = undefined; setBefore(undefined); setResults(null); setRetry(v => v + 1) }
  return (
    <Overlay onClose={close} labelledBy={`${id}-title`} className="max-h-dialog max-w-4xl overflow-y-auto">
      <div className="flex items-center gap-3 border-b border-line px-4 py-3">
        <Search className="h-4 w-4 shrink-0 text-brand" />
        <div className="min-w-0 flex-1">
          <h2 id={`${id}-title`} className="ui-section-title">{t("ui.components.overlays.ConversationSearch.589e8a71ea91")}</h2>
          <p className="truncate text-2xs text-faint" title={title}>{title}</p>
        </div>
        <IconButton label={t("ui.components.overlays.ConversationSearch.e0ac1ba9b32c")} onClick={close}><X className="h-4 w-4" /></IconButton>
      </div>
      <div className="border-b border-line px-4 py-3">
        <input data-conversation-search ref={input} autoFocus aria-label={t("ui.components.overlays.ConversationSearch.02db396b74ae")} maxLength={256} value={query}
          onChange={event => update(event.target.value)}
          onKeyDown={event => {
            if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
            if (event.key === 'Enter' && !loading && results?.results[0]) selectHit(results.results[0])
          }}
          placeholder={t("ui.components.overlays.ConversationSearch.73d7a58e1ea6")}
          className="h-9 w-full rounded-lg border border-line bg-raised px-3 text-sm text-ink placeholder:text-faint focus:border-brand/50 focus:outline-none" />
      <div className="refresh-action-container mt-2 flex items-center gap-2 text-2xs text-faint">
          <p className="min-w-0 flex-1">{t("ui.components.overlays.ConversationSearch.df2b078fc26f")}</p>
          {normalized && <RefreshButton size="xs" variant="ghost" label={t("ui.components.overlays.ConversationSearch.ab2eb6742d71")} onClick={refresh} disabled={loading} />}
        </div>
      </div>
      <div className={selection ? 'grid min-h-0 sm:grid-cols-search' : ''}>
        <div className={selection ? 'hidden min-w-0 sm:block sm:border-r sm:border-line' : 'min-w-0'}>
          <p role="status" aria-live="polite" className="px-4 py-2 text-2xs text-faint">
            {loading ? t("ui.components.overlays.ConversationSearch.1986994f3057") : results ? t("ui.components.overlays.ConversationSearch.cfdb6fbc5f30", {p0: (results.total), p1: (results.results.length)}) : t("ui.components.overlays.ConversationSearch.786959cf727f")}
          </p>
          <div className="max-h-search overflow-y-auto px-2 pb-3">
            {error ? <div role="alert" className="px-2 py-4 text-xs text-danger">{noticeText(error)}<Button size="xs" onClick={() => setRetry(v => v + 1)}>{t("ui.components.overlays.ConversationSearch.b8784c8dd563")}</Button></div> : null}
            {!normalized && <p className="px-2 py-8 text-center text-xs text-faint">{t("ui.components.overlays.ConversationSearch.b6f4f725321d")}</p>}
            {results?.total === 0 && <p className="px-2 py-8 text-center text-xs text-faint">{t("ui.components.overlays.ConversationSearch.a09f7ec4f5c2")}</p>}
            <ul aria-label={t("ui.components.overlays.ConversationSearch.78cf366afde1")} className="space-y-1">
              {results?.results.map(hit => <li key={hit.source_seq}>
                <button type="button" aria-pressed={selection?.hit.source_seq === hit.source_seq}
                  onClick={() => selectHit(hit)}
                  className="w-full rounded-lg border border-transparent px-3 py-3 text-left transition-colors hover:border-line hover:bg-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand aria-pressed:bg-brand/10">
                  <span className="mb-1 block text-2xs text-faint">{hit.role === 'user' ? t("ui.components.overlays.ConversationSearch.a0c7716669b5") : t("ui.components.overlays.ConversationSearch.e5ce5f6da7bd")} · {timestamp(hit.source_time)}</span>
                  <span className="block whitespace-pre-wrap break-words text-xs leading-relaxed text-muted [overflow-wrap:anywhere]">
                    {hit.prefix}<mark className="rounded bg-brand/15 px-0.5 text-ink">{hit.match}</mark>{hit.suffix}
                  </span>
                  <span className="mt-1.5 block text-2xs text-brand">{t("ui.components.overlays.ConversationSearch.e8952ea03f91")}</span>
                </button>
              </li>)}
            </ul>
            {results?.has_more && <ShowMoreControl className="mt-3" disabled={loading} onClick={() => setBefore(results.next_before ?? undefined)} />}
          </div>
        </div>
        {selection && <section aria-label={t("ui.components.overlays.ConversationSearch.e922d992eb62")} className="min-w-0">
          <div className="flex items-center gap-2 border-b border-line px-3 py-2">
            <Button size="xs" variant="ghost" icon={<ArrowLeft className="h-3 w-3" />} onClick={() => { setSelection(null); input.current?.focus() }}>{t("ui.components.overlays.ConversationSearch.887bd9bb113d")}</Button>
            <span className="min-w-0 flex-1 text-2xs text-faint">{selection.hit.role === 'user' ? t("ui.components.overlays.ConversationSearch.a0c7716669b5") : t("ui.components.overlays.ConversationSearch.e5ce5f6da7bd")} · {timestamp(selection.hit.source_time)}</span>
            {message && <CopyButton text={message.text} label={t("ui.components.overlays.ConversationSearch.b14e690de35a")} />}
          </div>
          {message && <div className="flex items-center gap-1 border-b border-line px-3 py-1">
            <Button size="xs" variant="ghost" active={!sourceView} aria-pressed={!sourceView} onClick={() => setSourceView(false)}>{t("ui.components.overlays.ConversationSearch.ce9b12499c47")}</Button>
            <Button size="xs" variant="ghost" active={sourceView} aria-pressed={sourceView} onClick={() => setSourceView(true)}>{t("ui.components.overlays.ConversationSearch.354b28c85333")}</Button>
          </div>}
          <div ref={reader} tabIndex={-1} aria-label={t("ui.components.overlays.ConversationSearch.c5e085837103")} className="max-h-reader overflow-y-auto p-4 focus:outline-none [overflow-wrap:anywhere]"
            onClick={event => {
              const link = (event.target as Element).closest('a')
              if (link?.getAttribute('href')?.startsWith('/api/v1/file?') && !link.hasAttribute('download')) close()
            }}>
            {reading && <p role="status" className="text-xs text-faint">{t("ui.components.overlays.ConversationSearch.3d0ef515e026")}</p>}
            {readError && <p role="alert" className="text-xs text-danger">{noticeText(readError)}<Button size="xs" onClick={() => setReadRetry(v => v + 1)}>{t("ui.components.overlays.ConversationSearch.1133d6759e10")}</Button></p>}
            {message && (sourceView ? <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-muted [overflow-wrap:anywhere]">
              {matchAt < 0 ? message.text : <>{message.text.slice(0, matchAt)}<mark data-search-match className="rounded bg-brand/20 px-0.5 text-ink">{selection!.hit.match}</mark>{message.text.slice(matchAt + selection!.hit.match.length)}</>}
            </pre> : <Markdown fileSource={{ path: '', root }}>{message.text}</Markdown>)}
          </div>
          {message && (message.previous_start !== null || message.next_start !== null) && <div className="flex items-center gap-2 border-t border-line px-3 py-2 text-2xs text-faint">
            <Button size="xs" disabled={message.previous_start === null} onClick={() => setSelection({ ...selection, start: message.previous_start! })}>{t("ui.components.overlays.ConversationSearch.5c73eb92d825")}</Button>
            <span className="min-w-0 flex-1 text-center">{message.start + 1}–{message.end} / {message.total_chars}{t("ui.components.overlays.ConversationSearch.25dcebd656bb")}</span>
            <Button size="xs" disabled={message.next_start === null} onClick={() => setSelection({ ...selection, start: message.next_start! })}>{t("ui.components.overlays.ConversationSearch.4cd4dc1f03d6")}</Button>
          </div>}
        </section>}
      </div>
    </Overlay>
  )
}
