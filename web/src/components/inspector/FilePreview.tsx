import { t, useLocale } from '@/i18n'
import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronUp, RefreshCw, Search, X } from 'lucide-react'
import { fileUrl, previewUrl, isHtmlPath, isImagePath, isTextFile, isVideoPath, isAudioPath, guessKind } from '@/lib/fileLinks'
import { findFileMatches, lineStarts, parseLineSelection, type FileLineRange } from '@/lib/fileNavigation'
import { languageFromPath } from '@/lib/diff'
import { highlightToHtml, isHighlightable } from '@/lib/highlight'
import { markHighlightedSource } from '@/lib/fileSourceHighlight'
import { useAppStore } from '@/store/app'
import { Markdown } from '@/components/markdown/Markdown'
import {SpecChart} from '@/components/markdown/SpecChart'
import {DataTable} from '@/components/markdown/DataTable'
import {isChartKind} from '@/lib/chartSpec'
import {isTableKind} from '@/lib/tableData'
import {readPreviewText} from '@/lib/filePreviewText'
import {visualizationFile} from '@/lib/visualizationFile'
import { FileOpenActions } from './FileOpenActions'

const MAX_BYTES = 256 * 1024
const LINE_HEIGHT = 24
const LINE_PADDING = 12

type PreviewMessage = { key: string; values?: Record<string, unknown> }
type PreviewNotice =
  | { kind: 'invalid' | 'outside'; total: number; truncated: boolean }
  | { kind: 'located' | 'reference'; range: FileLineRange }

function noticeText(notice: PreviewNotice): string {
  if ('range' in notice) return t(notice.kind === 'located' ? 'ui.components.inspector.FilePreview.cf9dbbb7dd22' : 'ui.components.inspector.FilePreview.89072647541c', {
    p0: notice.range.start, p1: notice.range.end === undefined ? '' : t('ui.filePreview.rangeEnd', { end: notice.range.end }),
  })
  return t(notice.kind === 'invalid' ? 'ui.components.inspector.FilePreview.f04b52622146' : 'ui.components.inspector.FilePreview.c7fc63e3d3b7', {
    p0: notice.total,
    p1: notice.kind === 'invalid' ? notice.truncated ? t('ui.components.inspector.FilePreview.5ee5ebd76e8f') : ''
      : t(notice.truncated ? 'ui.components.inspector.FilePreview.e524b4a0769b' : 'ui.components.inspector.FilePreview.1b3fa045e699'),
  })
}

/** 对话文件引用直接在侧栏预览；读取有上限，切换文件会取消旧请求。 */
export function FilePreview({ path, root, lineRange, navigationId, focusContent = true, active = true }: {
  path: string; root: string | null; lineRange?: FileLineRange; navigationId?: number; focusContent?: boolean; active?: boolean
}) {
  const locale = useLocale()

  const markdown = /\.(md|markdown)$/i.test(path)
  const html = isHtmlPath(path)
  const visualization = visualizationFile(path)
  const previewable = markdown || html || Boolean(visualization)
  const [view, setView] = useState<'reading' | 'source'>(previewable && !lineRange ? 'reading' : 'source')
  const reader = useRef<HTMLDivElement>(null)
  const readingScroll = useRef(0)
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState<PreviewMessage | null>(null)
  const [truncated, setTruncated] = useState(false)
  const [retry, setRetry] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const loadedScope = useRef<string | null>(null)
  const searchedQuery = useRef('')
  const [finding, setFinding] = useState(false)
  const [query, setQuery] = useState('')
  const [activeMatch, setActiveMatch] = useState(0)
  const [lineInput, setLineInput] = useState('')
  const [selected, setSelected] = useState<FileLineRange | null>(null)
  const [notice, setNotice] = useState<PreviewNotice | null>(null)
  const viewport = useRef<HTMLDivElement>(null)
  const findInput = useRef<HTMLInputElement>(null)
  const goInput = useRef<HTMLInputElement>(null)
  const lineComposing = useRef(false)
  const openImage = useAppStore((s) => s.openLightbox)
  const url = fileUrl(path, { root })
  const image = isImagePath(path), readable = isTextFile(path)
  const video = isVideoPath(path), audio = isAudioPath(path), pdf = guessKind(path) === 'pdf'
  const source = useMemo(() => text?.replace(/\r\n?/g, '\n') ?? '', [text, locale])
  const language = languageFromPath(path)
  const [syntax, setSyntax] = useState<{ source: string; language: string; html: string } | null>(null)
  const starts = useMemo(() => lineStarts(source), [source, locale])
  const lineNumbers = useMemo(() => starts.map((_, index) => index + 1).join('\n'), [starts, locale])
  const found = useMemo(() => findFileMatches(source, query, starts), [source, query, starts, locale])
  const syntaxHtml = useMemo(() => syntax?.source === source && syntax.language === language
    ? markHighlightedSource(syntax.html, source, found.matches, activeMatch) : null,
  [syntax, source, language, found, activeMatch, locale])
  const markedSource = useMemo(() => {
    if (!found.matches.length) return source
    const parts = []
    let previous = 0
    found.matches.forEach((hit, index) => {
      parts.push(source.slice(previous, hit.start))
      parts.push(<mark key={hit.start} data-active-match={index === activeMatch ? 'true' : undefined} className={index === activeMatch ? 'bg-brand/30 text-ink outline outline-1 outline-brand' : 'bg-warn/20 text-ink'}>{source.slice(hit.start, hit.end)}</mark>)
      previous = hit.end
    })
    parts.push(source.slice(previous))
    return parts
  }, [source, found, activeMatch, locale])
  useEffect(() => {
    if (text === null || view !== 'source' || !isHighlightable(language)) return
    let alive = true
    void highlightToHtml(source, language).then((html) => {
      if (alive && html !== null) setSyntax({ source, language, html })
    }).catch(() => { /* Keep the source readable if a language chunk fails to load. */ })
    return () => { alive = false }
  }, [source, text === null, language, view])
  function locate(range: FileLineRange, focus = false) {
    setSelected(range)
    const node = viewport.current
    if (!node) return
    node.scrollTop = Math.max(0, LINE_PADDING + (range.start - 1) * LINE_HEIGHT - node.clientHeight / 3)
    node.scrollLeft = 0
    if (focus) node.focus({ preventScroll: true })
  }
  function revealMatch() {
    requestAnimationFrame(() => {
      const node = viewport.current, mark = node?.querySelector('[data-active-match="true"]')
      if (!node || !mark) return
      const visible = node.getBoundingClientRect(), position = mark.getBoundingClientRect()
      // Scroll only this preview, keeping the conversation and other panels still.
      if (position.right > visible.right - 12) node.scrollLeft += position.right - visible.right + 12
      if (position.left < visible.left + 64) node.scrollLeft += position.left - visible.left - 64
    })
  }
  function moveMatch(direction: number) {
    if (!found.matches.length) return
    const index = (activeMatch + direction + found.matches.length) % found.matches.length
    const hit = found.matches[index]!
    setActiveMatch(index); setNotice(null)
    locate({ start: hit.line, end: hit.endLine })
    revealMatch()
  }
  function goToLine() {
    const range = parseLineSelection(lineInput, starts.length)
    if (!range) { setNotice({ kind: 'invalid', total: starts.length, truncated }); return }
    setNotice({ kind: 'located', range })
    locate(range, true)
  }
  useEffect(() => {
    const changed = query !== searchedQuery.current
    searchedQuery.current = query
    const index = changed ? 0 : Math.max(0, Math.min(activeMatch, found.matches.length - 1))
    setActiveMatch(index)
    if (query) {
      const hit = found.matches[index]
      setSelected(hit ? { start: hit.line, end: hit.endLine } : null)
      if (changed && hit) {
        locate({ start: hit.line, end: hit.endLine })
        revealMatch()
      }
    } else if (changed) setSelected(null)
    else setSelected(current => current && parseLineSelection(`${current.start}${current.end === undefined ? '' : '-' + current.end}`, starts.length) ? current : null)
  }, [found, query, activeMatch, starts.length])
  useEffect(() => {
    if (text === null || !lineRange || view !== 'source') return
    if (!parseLineSelection(`${lineRange.start}${lineRange.end !== undefined ? '-' + lineRange.end : ''}`, starts.length)) {
      setSelected(null); setNotice({ kind: 'outside', total: starts.length, truncated })
      return
    }
    setLineInput(String(lineRange.start)); setNotice({ kind: 'reference', range: lineRange })
    locate(lineRange, focusContent)
  }, [text, lineRange?.start, lineRange?.end, navigationId, starts.length, view])
  useEffect(() => { if (finding) findInput.current?.focus() }, [finding])
  useEffect(() => { setFinding(false); setQuery(''); readingScroll.current = 0; setView(previewable && !lineRange ? 'reading' : 'source') }, [navigationId, markdown, html, visualization, lineRange?.start, lineRange?.end])
  useEffect(() => {
    if (text === null || !focusContent) return
    if (view === 'reading' && reader.current) { reader.current.scrollTop = readingScroll.current; reader.current.focus({ preventScroll: true }) }
    else if (!finding) viewport.current?.focus({ preventScroll: true })
  }, [view, text, focusContent])
  useEffect(() => {
    if (!active) { setRefreshing(false); return }
    const scope = JSON.stringify([url, navigationId])
    if (loadedScope.current !== scope) { setText(null); setTruncated(false) }
    setError(null)
    if (!readable) { setRefreshing(false); return }
    setRefreshing(true)
    const abort = new AbortController()
    let alive = true
    void (async () => {
      const response = await fetch(url, { signal: abort.signal, cache: 'no-store', headers: { Range: `bytes=0-${MAX_BYTES - 1}` } })
      if (!response.ok) { if (alive) setError({ key: 'ui.components.inspector.FilePreview.d9f885d74720', values: { p0: response.status } }); return }
      const reader = response.body?.getReader()
      if (!reader) { if (alive) setError({ key: 'ui.components.inspector.FilePreview.c9e91b53401f' }); return }
      const total=Number(response.headers.get('Content-Range')?.split('/').pop() ?? response.headers.get('Content-Length') ?? 0)
      const {text:value,truncated:cut}=await readPreviewText(reader,MAX_BYTES,total)
      if (alive) { loadedScope.current = scope; setText(value); setTruncated(cut) }
    })().catch((failure) => { if (alive && !abort.signal.aborted) setError({ key: 'ui.filePreview.readFailure', values: { detail: String(failure instanceof Error ? failure.message : failure) } }) }).finally(() => { if (alive) setRefreshing(false) })
    return () => { alive = false; abort.abort() }
  }, [url, readable, retry, navigationId, active])
  return <section aria-busy={refreshing} role="region" aria-label={t("ui.components.inspector.FilePreview.f3c5fe4dd2da")} className="file-preview overflow-hidden rounded-lg border border-line" onKeyDown={(event) => {
    if (!readable || text === null || event.nativeEvent.isComposing || event.keyCode === 229) return
    if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'f') { event.preventDefault(); setView('source'); setFinding(true); findInput.current?.focus() }
    if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'g') { event.preventDefault(); setView('source'); requestAnimationFrame(() => { goInput.current?.focus(); goInput.current?.select() }) }
    if (event.key === 'F3' && finding) { event.preventDefault(); moveMatch(event.shiftKey ? -1 : 1) }
  }}>
    <header className="file-preview-header flex items-center gap-2 border-b border-line px-3 py-2 text-xs">
      <span className="min-w-0 flex-1 truncate font-medium" title={path}>{path.split(/[\\/]/).pop()}</span>
      {previewable && text !== null && !error && !(visualization && truncated) ? <button type="button" className="file-view-toggle" onClick={() => {
        setView(view === 'reading' ? 'source' : 'reading')
        if (view === 'source') { setFinding(false); setQuery('') }
      }}>{view === 'reading' ? t("ui.components.inspector.FilePreview.035440298e53") : t(visualization ? 'ui.widget.viewWidget' : 'ui.components.inspector.FilePreview.13495f8f1914')}</button> : null}
      {readable && text !== null ? <button type="button" disabled={refreshing} aria-label={t("ui.components.inspector.FilePreview.bac6fbe5d699")} title={t("ui.components.inspector.FilePreview.5857e5b72d49")} onClick={() => setRetry((value) => value + 1)} className="text-muted hover:text-ink"><RefreshCw aria-hidden className={refreshing ? "h-3 w-3 animate-spin" : "h-3 w-3"} /></button> : null}
      <FileOpenActions path={path} root={root} />
    </header>
    {image ? <button type="button" aria-label={t("ui.components.inspector.FilePreview.ba742ac7dcaa")} onClick={() => openImage(url, path)} className="file-preview-image block w-full p-2"><img src={url} alt={path} className="max-h-80 w-full object-contain" onError={() => setError({ key: 'ui.components.inspector.FilePreview.effa0053fe92' })} /></button> : null}
    {error ? <div role="alert" className="space-y-2 p-3 text-xs text-danger"><p>{t(error.key, error.values)}</p>{readable && text === null ? <button type="button" onClick={() => setRetry((value) => value + 1)} className="text-ink underline">{t("ui.components.inspector.FilePreview.5857e5b72d49")}</button> : null}</div> : null}
    {readable ? text === null || loadedScope.current !== JSON.stringify([url,navigationId]) ? !error ? <p className="p-3 text-xs text-muted">{t("ui.components.inspector.FilePreview.b75809afbe0c")}</p> : null : visualization && view === 'reading' && !truncated ? <div className="p-3">{isTableKind(visualization) ? <DataTable key={url} source={text} kind={visualization} title={path.split(/[\\/]/).at(-1)} /> : isChartKind(visualization) ? <SpecChart key={url} source={text} kind={visualization} active={active} /> : null}</div> : html && view === 'reading' ? <iframe key={url} src={previewUrl(path, { root })} title={t('ui.filePreview.htmlPreview')} sandbox="allow-scripts" className="h-file-reader w-full border-0 bg-white" /> : markdown && view === 'reading' ? <div ref={reader} tabIndex={0} role="region" aria-label={t("ui.components.inspector.FilePreview.96183d9557ac")} data-markdown-reader onScroll={(event) => { readingScroll.current = event.currentTarget.scrollTop }} className="max-h-file-reader overflow-auto px-4 py-3 focus-visible:outline focus-visible:outline-1 focus-visible:outline-brand">
      <Markdown fileSource={{ path, root }} className="w-full min-w-0 text-compact leading-7 [&>p]:break-words [&>h1:first-child]:mt-0">{text}</Markdown>
    </div> : <>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2 text-xs">
        <button type="button" aria-expanded={finding} onClick={() => setFinding((value) => !value)} className="inline-flex items-center gap-1 text-muted hover:text-ink"><Search aria-hidden className="h-3 w-3" />{t("ui.components.inspector.FilePreview.3003318e729a")}</button>
        <form className="ml-auto flex min-w-0 items-center gap-1.5" onSubmit={(event) => { event.preventDefault(); if (!lineComposing.current) goToLine() }}>
          <label className="sr-only" htmlFor="file-line-location">{t("ui.components.inspector.FilePreview.1a21661f567c")}</label>
          <input ref={goInput} id="file-line-location" onCompositionStart={() => { lineComposing.current = true }} onCompositionEnd={() => { lineComposing.current = false }} onBlur={() => { lineComposing.current = false }} onKeyDown={(event) => {
            if (event.key === 'Enter' && (lineComposing.current || event.nativeEvent.isComposing || event.keyCode === 229)) { event.preventDefault(); event.stopPropagation() }
          }} value={lineInput} maxLength={40} onChange={(event) => setLineInput(event.target.value)} placeholder={t("ui.components.inspector.FilePreview.29d86f38eb04")} className="w-20 rounded border border-line bg-canvas px-1.5 py-1 text-ink" />
          <button type="submit" className="text-muted hover:text-ink">{t("ui.components.inspector.FilePreview.49726f09c812")}</button>
        </form>
        <span className="text-faint">{t(starts.length === 1 ? truncated ? 'ui.filePreview.oneLinePreview' : 'ui.filePreview.oneLine' : truncated ? 'ui.filePreview.lineCountPreview' : 'ui.filePreview.lineCount', { count: starts.length })}</span>
      </div>
      {finding ? <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-2 text-xs">
        <input ref={findInput} aria-label={t("ui.components.inspector.FilePreview.3d83fb8cd0d6")} value={query} maxLength={256} placeholder={t("ui.components.inspector.FilePreview.bbbf1ca5b314")} onChange={(event) => { setQuery(event.target.value); setNotice(null) }} onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || event.keyCode === 229) return
          if (event.key === 'Enter') { event.preventDefault(); moveMatch(event.shiftKey ? -1 : 1) }
          if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setFinding(false); setQuery(''); viewport.current?.focus({ preventScroll: true }) }
        }} className="min-w-0 flex-1 rounded border border-line bg-canvas px-2 py-1 text-ink" />
        <span role="status" className="text-muted">{query ? found.matches.length ? t(found.capped ? 'ui.filePreview.cappedMatches' : 'ui.filePreview.matchCount', { current: activeMatch + 1, total: found.matches.length }) : t("ui.components.inspector.FilePreview.12129b289261") : t("ui.components.inspector.FilePreview.7159815dd39f")}</span>
        <button type="button" aria-label={t("ui.components.inspector.FilePreview.64d57ad378df")} disabled={!found.matches.length} onClick={() => moveMatch(-1)} className="rounded p-1 text-muted disabled:opacity-40"><ChevronUp className="h-3.5 w-3.5" /></button>
        <button type="button" aria-label={t("ui.components.inspector.FilePreview.84cb6a31b9f5")} disabled={!found.matches.length} onClick={() => moveMatch(1)} className="rounded p-1 text-muted disabled:opacity-40"><ChevronDown className="h-3.5 w-3.5" /></button>
        <button type="button" aria-label={t("ui.components.inspector.FilePreview.02df518db566")} onClick={() => { setFinding(false); setQuery(''); viewport.current?.focus({ preventScroll: true }) }} className="rounded p-1 text-muted"><X className="h-3.5 w-3.5" /></button>
      </div> : null}
      {notice ? <p role="status" className="border-b border-line px-3 py-2 text-xs text-muted">{noticeText(notice)}</p> : null}
      <div ref={viewport} role="region" aria-label={t("ui.components.inspector.FilePreview.18b7e9450e54")} tabIndex={0} className="file-preview-viewport max-h-file-source overflow-auto bg-surface font-mono text-xs text-ink">
        <div className="relative flex min-w-full w-max items-stretch">
          {selected ? <div aria-hidden="true" data-file-highlight={`${selected.start}-${selected.end ?? selected.start}`} className="pointer-events-none absolute left-0 right-0 border-l-2 border-brand bg-brand/10" style={{ top: LINE_PADDING + (selected.start - 1) * LINE_HEIGHT, height: ((selected.end ?? selected.start) - selected.start + 1) * LINE_HEIGHT }} /> : null}
          <pre aria-hidden="true" data-file-line-numbers style={{ lineHeight: `${LINE_HEIGHT}px`, paddingTop: LINE_PADDING, paddingBottom: LINE_PADDING }} className="sticky left-0 z-10 m-0 min-w-14 shrink-0 select-none border-r-gutter border-white bg-raised px-3 text-right font-mono text-xs tabular-nums text-muted dark:border-white/15">{lineNumbers}</pre>
          <pre data-file-source data-source-language={language || undefined} style={{ lineHeight: `${LINE_HEIGHT}px`, paddingTop: LINE_PADDING, paddingBottom: LINE_PADDING }} className="file-preview-source relative m-0 flex-1 whitespace-pre px-3 font-mono text-xs">{syntaxHtml === null ? markedSource : <span data-file-syntax dangerouslySetInnerHTML={{ __html: syntaxHtml }} />}</pre>
        </div>
      </div>
    </> : !image && !video && !audio && !pdf ? <p className="p-3 text-xs text-muted">{t("ui.components.inspector.FilePreview.30d30d91eb3c")}</p> : null}
    {video ? <video key={url} src={url} controls preload="metadata" aria-label={t("ui.components.inspector.FilePreview.fe1e894a2383")} className="max-h-media w-full" onError={() => setError({ key: 'ui.components.inspector.FilePreview.fec966685ac8' })} /> : null}
    {audio ? <audio key={url} src={url} controls preload="metadata" aria-label={t("ui.components.inspector.FilePreview.130d724ae3d9")} className="my-3 w-full" onError={() => setError({ key: 'ui.components.inspector.FilePreview.826521a45354' })} /> : null}
    {pdf ? <iframe key={url} src={url} title={t("ui.components.inspector.FilePreview.c858973fd6cf")} className="h-file-reader w-full border-0" /> : null}
    {truncated ? <p className="border-t border-line px-3 py-2 text-xs text-muted">{t("ui.components.inspector.FilePreview.cd2f0e54f631")}</p> : null}
  </section>
}
