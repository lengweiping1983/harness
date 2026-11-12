import {useEffect, useRef, useState} from 'react'
import {AlertTriangle, Loader2, RefreshCw} from 'lucide-react'
import {Button} from '@/components/ui/primitives'
import {t, useLocale} from '@/i18n'
import {useAppStore} from '@/store/app'

type ServiceStatus = 'running' | 'starting' | 'stopped'
type Bridge = {browser: (action: string) => Promise<{status: ServiceStatus}>; onServiceState: (listener: (status: ServiceStatus) => void) => () => void}

export function DesktopServiceStatus() {
  useLocale()
  const [status, setStatus] = useState<ServiceStatus>('running')
  const [failed, setFailed] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const restart = useRef<() => void>(() => {})
  const desktop = (window as unknown as {harnessDesktop?: Bridge}).harnessDesktop
  useEffect(() => {
    if (!desktop) return
    let previous: ServiceStatus = 'running'
    let revision = 0
    let disposed = false
    let pending = false
    setRestarting(false)
    const receive = (next: ServiceStatus) => {
      if (disposed) return
      if (next === 'running' && previous !== 'running') {
        window.dispatchEvent(new Event('harness-service-restored'))
        void useAppStore.getState().boot()
      }
      previous = next
      setFailed(false)
      setStatus(next)
    }
    restart.current = () => {
      if (disposed || pending || previous === 'starting') return
      pending = true
      setRestarting(true)
      const ticket = ++revision
      receive('starting')
      void desktop.browser('restart_backend').then(result => {
        if (ticket === revision) receive(result.status)
      }).catch(() => {
        if (!disposed && ticket === revision) {
          receive('stopped')
          setFailed(true)
        }
      }).finally(() => {
        pending = false
        if (!disposed) setRestarting(false)
      })
    }
    const unsubscribe = desktop.onServiceState(next => {revision++; receive(next)})
    const ticket = revision
    void desktop.browser('service_state').then(result => {if (ticket === revision) receive(result.status)}).catch(() => {if (ticket === revision) receive('stopped')})
    return () => {disposed = true; unsubscribe()}
  }, [desktop])
  if (!desktop || status === 'running') return null
  return <div role="alert" className="fixed left-4 top-4 z-status max-w-sm rounded-xl border border-line bg-surface p-4 shadow-xl">
    <div className="flex items-start gap-3"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn"/><div className="space-y-2"><h2 className="text-sm font-semibold">{t(status === 'starting' ? 'browser.desktop.serviceStarting' : 'browser.desktop.serviceStopped')}</h2><p className="text-xs leading-relaxed text-muted">{t(failed ? 'browser.desktop.serviceRetryFailed' : 'browser.desktop.servicePreserved')}</p><Button size="sm" disabled={status === 'starting' || restarting} onClick={() => restart.current()}>{status === 'starting' ? <Loader2 className="h-3.5 w-3.5 animate-spin"/> : <RefreshCw className="h-3.5 w-3.5"/>}{t('browser.desktop.serviceReconnect')}</Button></div></div>
  </div>
}
