import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { failureText } from '@/lib/resourceError'
import { useEffect, useRef, useState } from 'react'
import { t, useLocale } from '@/i18n'
import { runtimeRequest } from '@/lib/http'
import { Button, RefreshButton } from '@/components/ui/primitives'
import { DiffView } from '@/components/chat/blocks/DiffView'
import { usePageVisible } from '@/hooks/usePageVisible'

type Review = { base_commit: string; base_kind: 'creation'; files: Array<{path: string; status: string; kind: string}>; selection?: {path: string; diff: string; truncated: boolean; unavailable: string | null} }
export function WorktreeReview({ id, defaultOpen = false, active = true }: { id: string; defaultOpen?: boolean; active?: boolean }) {
  useLocale()
  const visible = usePageVisible() && active
  const [open, setOpen] = useState(defaultOpen)
  const [data, setData] = useState<Review | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const generation = useRef(0)
  const reading = useRef<AbortController>()
  const selectedPath = useRef<string>()
  useEffect(() => {
    if (open && visible) void load(selectedPath.current)
    return () => { generation.current++; reading.current?.abort() }
  }, [id, open, visible])
  async function load(path?: string) {
    if (!visible) return
    selectedPath.current = path
    reading.current?.abort()
    const controller = reading.current = new AbortController()
    const current = ++generation.current
    setLoading(true); setError(''); setData(previous => previous ? {...previous, selection: undefined} : null)
    try {
      const result = await runtimeRequest<Review>('/worktrees/' + encodeURIComponent(id) + '/review' + (path !== undefined ? '?path=' + encodeURIComponent(path) : ''), 'GET', undefined, {signal: controller.signal})
      if (generation.current === current) setData(result)
    } catch (failure) { if (generation.current === current) setError(failureText(failure)) }
    finally { if (generation.current === current) setLoading(false) }
  }
  return <div className="min-w-0 space-y-3">
    <Button size="sm" aria-expanded={open} onClick={() => setOpen(!open)}>{t(open ? 'ui.worktree.review.close' : 'ui.worktree.review.open')}</Button>
    {open && <section className="min-w-0 space-y-3 border-t border-line pt-3" aria-label={t('ui.worktree.review.heading')} aria-busy={loading}>
      <div className="refresh-action-container flex flex-wrap items-center justify-between gap-2"><p className="text-xs text-muted">{t('ui.worktree.review.baseline')} {data?.base_commit.slice(0, 12)}</p><RefreshButton size="xs" label={t('ui.worktrees.refresh')} disabled={loading} onClick={() => void load(data?.selection?.path)} /></div>
      {error && <p role="alert" className="break-words text-xs text-danger">{noticeText(error)}</p>}
      {loading && <p role="status" className="text-xs text-muted">{t('ui.worktree.review.loading')}</p>}
      {data && !data.files.length && <p className="text-xs text-muted">{t('ui.worktree.review.empty')}</p>}
      {Boolean(data?.files.length) && <div className="max-h-52 space-y-1 overflow-auto rounded-lg border border-line p-2">{data?.files.map(file => <button type="button" key={file.path} disabled={loading} aria-pressed={data.selection?.path === file.path} onClick={() => void load(file.path)} className="flex w-full min-w-0 gap-2 rounded px-2 py-1.5 text-left text-xs hover:bg-raised disabled:opacity-50"><code className="shrink-0 text-brand">{file.status}</code><span className="min-w-0 break-all">{file.path}</span>{file.kind === 'untracked' && <span className="ml-auto shrink-0 text-faint">{t('ui.worktree.review.untracked')}</span>}</button>)}</div>}
      {data?.selection && <div className="min-w-0 space-y-2"><p className="break-all font-mono text-xs text-muted">{data.selection.path}</p>
        {data.selection.unavailable ? <p className="text-xs text-muted">{t('ui.worktree.review.' + data.selection.unavailable)}</p> : data.selection.diff ? <><DiffView output={{diff:data.selection.diff}} path={data.selection.path} /><details><summary className="cursor-pointer text-2xs text-muted">{t('ui.worktree.review.raw')}</summary><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-all rounded bg-canvas p-2 font-mono text-2xs">{data.selection.diff}</pre></details></> : <p className="text-xs text-muted">{t('ui.worktree.review.noText')}</p>}
        {data.selection.truncated && <p className="text-xs text-warn">{t('ui.worktree.review.truncated')}</p>}
      </div>}
    </section>}
  </div>
}
