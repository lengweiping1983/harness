import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { useEffect, useId, useRef, useState } from 'react'
import { ArrowUp, Check, Folder, HardDrive, Home, Monitor, X } from 'lucide-react'
import { ApiError, runtimeRequest } from '@/lib/http'
import type { DirectoryListing } from '@/types'
import { Button } from './primitives'
import { t, useLocale } from '@/i18n'

/** 不注册项目的内联目录选择器，页面卸载后丢弃异步结果。 */
export function DirectoryField({ value, onChange, label, disabled }: { value: string; onChange: (value: string) => void; label: string; disabled?: boolean }) {
  useLocale()
  const directoryId = useId()
  const [open, setOpen] = useState(false)
  const [listing, setListing] = useState<DirectoryListing | null>(null)
  const [error, setError] = useState<ResourceText>('')
  const [loading, setLoading] = useState(false)
  const epoch = useRef(0)
  const reading = useRef<AbortController>()
  useEffect(() => () => { epoch.current += 1; reading.current?.abort() }, [])
  const close = () => { epoch.current += 1; reading.current?.abort(); setOpen(false); setLoading(false) }
  const load = async (path?: string) => {
    reading.current?.abort()
    const controller = reading.current = new AbortController()
    const request = ++epoch.current
    setLoading(true); setError(''); setListing(null); setOpen(true)
    try {
      const result = await runtimeRequest<DirectoryListing>('/directories' + (path ? '?path=' + encodeURIComponent(path) : ''), 'GET', undefined, {signal: controller.signal})
      if (request === epoch.current) setListing(result)
    } catch (failure) { if (request === epoch.current) setError(failure instanceof ApiError && failure.failure.copy ? failure.failure.copy : (failure as Error).message) }
    finally { if (request === epoch.current) setLoading(false) }
  }
  return <div className="space-y-2">
    <div className="flex gap-2">
      <input aria-label={label} value={value} disabled={disabled} onChange={event => onChange(event.target.value)} placeholder={label} className="ui-field min-w-0 flex-1 font-mono" />
      <Button size="sm" disabled={disabled} aria-label={t('ui.directory.browseFor', {label})} aria-expanded={open} aria-controls={open ? directoryId : undefined} icon={<Folder className="h-3.5 w-3.5" />} onClick={() => { if (open) close(); else void load(value || undefined) }}>{t('ui.directory.browse')}</Button>
    </div>
    {open && <section id={directoryId} aria-label={t('ui.extensions.directory')} className="space-y-2 rounded-lg border border-line bg-raised/30 p-3">
      <div className="flex items-center gap-2">
        <Button size="xs" variant="ghost" disabled={disabled || loading || (!listing?.parent && !(listing?.path && listing.roots?.length))} aria-label={t('ui.extensions.parent')} onClick={() => void load(listing?.parent ?? '::computer')}><ArrowUp className="h-3.5 w-3.5" /></Button>
        <Button size="xs" variant="ghost" disabled={disabled || loading} aria-label={t('ui.components.overlays.WorkspacePicker.6bff6a717a8c')} onClick={() => void load()}><Home className="h-3.5 w-3.5" /></Button>
        {listing?.roots?.length ? <Button size="xs" variant="ghost" disabled={disabled || loading} aria-label={t('ui.workspacePicker.computer')} onClick={() => void load('::computer')}><Monitor className="h-3.5 w-3.5" /></Button> : null}
        <span className="min-w-0 flex-1 break-all font-mono text-2xs text-muted">{listing?.path || (listing ? t('ui.workspacePicker.computer') : '')}</span>
        <Button size="xs" variant="ghost" aria-label={t('ui.extensions.closeDirectory')} onClick={close}><X className="h-3.5 w-3.5" /></Button>
      </div>
      {error && <p role="alert" className="text-xs text-danger">{noticeText(error)}</p>}
      {loading ? <p role="status" className="text-xs text-muted">{t('ui.extensions.loading')}</p> : <div className="max-h-48 space-y-1 overflow-y-auto">
        {listing?.entries.map(entry => <button type="button" disabled={disabled} key={entry.path} className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs text-ink hover:bg-raised focus-visible:outline focus-visible:outline-brand" onClick={() => void load(entry.path)}>{listing.path ? <Folder className="h-3.5 w-3.5 shrink-0 text-faint" /> : <HardDrive className="h-3.5 w-3.5 shrink-0 text-faint" />}<span className="min-w-0 break-all">{entry.name}</span></button>)}
        {listing && !listing.entries.length && <p className="py-2 text-xs text-muted">{t('ui.extensions.noSubdirectories')}</p>}
        {listing?.truncated && <p className="text-2xs text-warn">{t('ui.extensions.directoryTruncated')}</p>}
      </div>}
      <Button size="sm" disabled={disabled || !listing?.path || loading} icon={<Check className="h-3.5 w-3.5" />} onClick={() => { if (listing) { onChange(listing.path); close() } }}>{t('ui.extensions.useDirectory')}</Button>
    </section>}
  </div>
}
