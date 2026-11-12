import {useEffect, useState} from 'react'
import {api} from '@/lib/api'
import {t, useLocale} from '@/i18n'
import {Loader2, RefreshCw} from 'lucide-react'
import {IconButton} from '@/components/ui/primitives'
import type {TerminalTypes} from '@/lib/protocol.generated'

import {preferredTerminalType, rememberTerminalType} from '@/lib/terminalTypePreference'
import {noticeText} from '@/lib/noticeText'
import type {ResourceText} from '@/types'

type Catalog = {status: 'loading'} | {status: 'error'; message: ResourceText} | {status: 'ready'; data: TerminalTypes}

export function TerminalTypeSelect({active}: {active: boolean}) {
  useLocale()
  const [catalog, setCatalog] = useState<Catalog>({status: 'loading'})
  const [kind, setKind] = useState(preferredTerminalType)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!active) return
    setCatalog({status: 'loading'})
    const controller = new AbortController()
    void api.terminalTypes(controller.signal).then(result => {
      if (controller.signal.aborted) return
      if (!result.ok) {setCatalog({status: 'error', message: result.copy ?? result.message}); return}
      setCatalog({status: 'ready', data: result.data})
      setKind(current => {
        const preferred = preferredTerminalType() || current
        return result.data.backends.includes(preferred) ? preferred
          : result.data.backends.includes(result.data.default_type) ? result.data.default_type : result.data.backends[0] ?? ''
      })
    })
    return () => controller.abort()
  }, [active, retry])
  if (!active) return null
  if (catalog.status === 'loading') return <span role="status" className="flex items-center gap-1.5 text-xs text-muted"><Loader2 aria-hidden="true" className="h-3 w-3 shrink-0 animate-spin" />{t('ui.terminalTypes.loading')}</span>
  if (catalog.status === 'error' || !catalog.data.enabled || !catalog.data.backends.length) return <div className="flex max-w-64 items-center gap-1.5">
    {catalog.status === 'error'
      ? <p role="alert" className="min-w-0 break-words text-xs text-danger">{t('ui.terminalTypes.failed', {error: noticeText(catalog.message)})}</p>
      : <p role="status" className="min-w-0 text-xs text-muted">{t(catalog.data.enabled ? 'ui.terminalTypes.empty' : 'ui.components.inspector.TerminalWorkbench.9279dea372ba')}</p>}
    <IconButton size="sm" className="shrink-0" label={t('ui.terminalTypes.reload')} onClick={() => setRetry(value => value + 1)}><RefreshCw className="h-3.5 w-3.5" /></IconButton>
  </div>
  return <select aria-label={t('ui.components.inspector.TerminalWorkbench.56d01b61b87a')} value={kind} onChange={event => {setKind(event.target.value); rememberTerminalType(event.target.value)}} className="min-w-0 max-w-36 rounded-md border border-line bg-raised px-2 py-1 text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand">
    {catalog.data.backends.map(type => <option key={type} value={type}>{type}</option>)}
  </select>
}
