import {NativeBrowserPanel, desktopBrowser} from './NativeBrowserPanel'
import {t, useLocale} from '@/i18n'
import {Globe, Monitor} from 'lucide-react'
import {CopyButton, EmptyHint} from '@/components/ui/primitives'

export function BrowserPanel({sessionId, active = true}: {sessionId: string | null; active?: boolean}) {
  useLocale()
  if (!desktopBrowser()) return <DesktopBrowserEntry />
  return sessionId ? <NativeBrowserPanel key={sessionId} sessionId={sessionId} active={active} /> : <EmptyHint icon={<Globe className="h-6 w-6"/>} title={t('ui.components.inspector.BrowserPanel.398cc0ba1a57')} description={t('ui.components.inspector.BrowserPanel.afb38975af4b')} />
}

export function DesktopBrowserEntry() {
  useLocale()
  const command = 'node desktop/launch.mjs'
  return <div className="flex h-full flex-col items-center justify-center gap-5 p-6 text-center">
    <div className="rounded-2xl border border-line bg-raised p-4"><Monitor className="h-8 w-8 text-brand"/></div>
    <div className="space-y-2"><h3 className="text-sm font-medium text-ink">{t('browser.desktop.entry.title')}</h3><p className="max-w-sm text-xs leading-relaxed text-muted">{t('browser.desktop.entry.description')}</p></div>
    <div className="w-full max-w-sm rounded-xl border border-line bg-surface p-3 text-left">
      <p className="mb-2 text-xs text-muted">{t('browser.desktop.entry.run')}</p>
      <div className="flex items-center gap-2 rounded-lg bg-canvas p-2"><code className="min-w-0 flex-1 select-text break-all text-xs text-ink">{command}</code><CopyButton text={command} label={t('browser.desktop.entry.copy')}/></div>
    </div>
    <p className="max-w-sm text-xs leading-relaxed text-faint">{t('browser.desktop.entry.requirements')}</p>
  </div>
}
