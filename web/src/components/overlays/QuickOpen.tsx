import type { ResourceText } from '@/types'
import { failureText } from '@/lib/resourceError'
import { noticeText } from '@/lib/noticeText'
import { useEffect, useId, useRef, useState } from 'react'
import { File, Folder, Loader2, Search, X } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { useFileReferences } from '@/hooks/useFileReferences'
import { EMPTY_FILE_HISTORY, filePreviewScope, type FilePreviewReference } from '@/lib/filePreviewHistory'
import { basenameOf, normalizePath, sessionDirectory } from '@/lib/workspaces'
import { cn } from '@/lib/utils'
import { ensureFileWorkspace } from '@/lib/fileWorkspace'
import { useActiveSession, useAppStore } from '@/store/app'
import { Button, IconButton, Kbd, Overlay } from '@/components/ui/primitives'

interface QuickEntry { path: string; directory: boolean; recent: boolean }

/** 无搜索词时把本目录的近期文件提到结果前面，按路径去重。 */
function mergeRecentEntries(listed: QuickEntry[], recent: FilePreviewReference[], root: string): QuickEntry[] {
  const previous = [...recent].reverse()
    .filter(item => item.root && normalizePath(item.root) === normalizePath(root))
    .map(item => ({ path: item.path, directory: false, recent: true }))
  const seen = new Set<string>()
  return [...previous, ...listed].filter(item => {
    const key = JSON.stringify([item.path, item.directory])
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function QuickOpen() {
  const open = useAppStore(state => state.quickOpen)
  const session = useActiveSession()
  if (!open || !session) return null
  return <QuickOpenDialog key={session.id + '\0' + sessionDirectory(session)} sessionId={session.id} root={sessionDirectory(session)} />
}

function QuickOpenDialog({ sessionId, root }: {sessionId: string; root: string | null}) {
  useLocale()
  const id = useId()
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [opening, setOpening] = useState(false)
  const [openError, setOpenError] = useState<ResourceText>('')
  const pending = useRef(false)
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const recent = useAppStore(state => state.inspectorFileHistory[filePreviewScope(sessionId)] ?? EMPTY_FILE_HISTORY)
  const files = useFileReferences(sessionId, query.trim(), root)
  const list = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => { if (!opening && openError) input.current?.focus() }, [opening, openError])
  const close = () => useAppStore.getState().setQuickOpen(false)
  const result = files.result
  const projectRoot = result?.root ?? root
  const listed: QuickEntry[] = result?.matches.map(item => ({path: item.path, directory: item.kind === 'directory', recent: false})) ?? []
  const entries = result && !query.trim() ? mergeRecentEntries(listed, recent, result.root).slice(0, 40) : listed
  const keyOf = (item: typeof entries[number]) => JSON.stringify([item.path, item.directory])
  const cursor = Math.max(0, entries.findIndex(item => keyOf(item) === selected))
  const optionId = (index: number) => `${id}-option-${index}`
  useEffect(() => { list.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView({block: 'nearest'}) }, [cursor, result])
  async function openAt(index: number) {
    const item = entries[index]
    const store = useAppStore.getState()
    if (!item || !result || files.loading || pending.current || store.activeSessionId !== sessionId) return
    pending.current = true; setOpening(true); setOpenError('')
    try {
      await ensureFileWorkspace(result.root)
      const current = useAppStore.getState()
      if (!alive.current || current.activeSessionId !== sessionId) return
      const project = current.workspaces.find(project => normalizePath(project.path) === normalizePath(result.root))
      current.selectWorkspace(project?.id ?? null)
      close()
      current.revealInFiles(item.path, undefined, {root: result.root, directory: item.directory, focusContent: true})
    } catch (error) {
      if (alive.current) setOpenError(failureText(error))
    } finally {
      pending.current = false
      if (alive.current) {
        setOpening(false)
      }
    }
  }
  return <Overlay onClose={close} labelledBy={`${id}-title`} className="flex max-h-dialog max-w-2xl flex-col">
    <header className="flex items-center gap-3 border-b border-line px-4 py-3">
      <Search className="h-4 w-4 shrink-0 text-brand" />
      <div className="min-w-0 flex-1"><h2 id={`${id}-title`} className="text-sm font-semibold">{t('ui.quickOpen.title')}</h2><p className="flex min-w-0 gap-2 text-2xs text-faint" title={projectRoot ?? ''}><span className="max-w-project shrink-0 truncate font-medium text-muted">{projectRoot ? basenameOf(projectRoot) : ''}</span><span className="truncate">{projectRoot}</span></p></div>
      <IconButton label={t('ui.quickOpen.close')} onClick={close}><X className="h-4 w-4" /></IconButton>
    </header>
    <div className="px-4 py-3"><input ref={input} autoFocus data-quick-open type="text" role="combobox" aria-label={t('ui.quickOpen.query')} aria-autocomplete="list" aria-expanded="true" aria-controls={`${id}-results`} aria-activedescendant={entries.length ? optionId(cursor) : undefined} maxLength={256} value={query} placeholder={t('ui.quickOpen.placeholder')}
      disabled={opening} onChange={event => { setQuery(event.target.value); setSelected(null); setOpenError('') }}
      onKeyDown={event => {
        if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault()
          if (entries.length) setSelected(keyOf(entries[(cursor + (event.key === 'ArrowDown' ? 1 : -1) + entries.length) % entries.length]))
        } else if (event.key === 'Enter') { event.preventDefault(); void openAt(cursor) }
      }} className="h-10 w-full rounded-lg border border-line bg-raised px-3 text-sm placeholder:text-faint focus:border-brand/50 focus:outline-none" /></div>
    <div ref={list} id={`${id}-results`} role="listbox" aria-label={t('ui.quickOpen.results')} aria-busy={files.loading} className="min-h-0 overflow-y-auto px-2 pb-2">
      {entries.map((item, index) => { const Icon = item.directory ? Folder : File; return <button key={keyOf(item)} id={optionId(index)} type="button" disabled={opening} tabIndex={-1} role="option" aria-selected={index === cursor} onMouseEnter={() => setSelected(keyOf(item))} onClick={() => void openAt(index)} className={cn('flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm', index === cursor ? 'bg-brand/10 text-brand' : 'text-muted hover:bg-raised')}>
        <Icon className="h-4 w-4 shrink-0" /><span className="min-w-0 flex-1"><span className="block truncate font-medium">{item.path.split('/').at(-1)}</span><span className="block truncate text-2xs text-faint" title={item.path}>{item.path}</span></span>{item.recent && <span className="shrink-0 text-2xs text-faint">{t('ui.quickOpen.recent')}</span>}
      </button> })}
    </div>
    {opening && <p role="status" className="flex items-center gap-2 px-5 py-2 text-xs text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" />{t('ui.quickOpen.opening')}</p>}
    {openError && <p role="alert" className="break-words px-5 py-2 text-xs text-danger">{noticeText(openError)}</p>}
    {files.loading ? <p role="status" className="flex items-center gap-2 px-5 py-4 text-xs text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" />{t('ui.quickOpen.loading')}</p> : files.error ? <div className="space-y-2 px-5 py-4"><p role="alert" className="break-words text-xs text-danger">{files.error}</p><Button size="sm" onClick={() => { files.retry(); input.current?.focus() }}>{t('ui.quickOpen.retry')}</Button></div> : !entries.length ? <p role="status" className="px-5 py-4 text-xs text-muted">{t('ui.quickOpen.empty')}</p> : null}
    <footer className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-2 text-2xs text-faint"><span><Kbd>↑↓</Kbd> {t('ui.quickOpen.choose')}</span><span><Kbd>Enter</Kbd> {t('ui.quickOpen.open')}</span><span><Kbd>Esc</Kbd> {t('ui.quickOpen.close')}</span>{result && (result.has_more || result.truncated) && <span className="min-w-0">{t('ui.quickOpen.narrow')}</span>}</footer>
  </Overlay>
}
