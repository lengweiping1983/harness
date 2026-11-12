import { useState } from 'react'
import { t, useLocale } from '@/i18n'
import { Button, RefreshButton } from '@/components/ui/primitives'
import { copyText } from '@/lib/utils'
import { externalRunnerReadiness, type ExternalAgentCatalog } from '@/lib/externalAgents'

export function ExternalRunnerStatus({ runner, catalog, loading, error, refresh }: { runner: string; catalog: ExternalAgentCatalog | null; loading: boolean; error: string; refresh: () => void }) {
  useLocale()
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState(false)
  const state = externalRunnerReadiness(catalog, runner)
  const entry = catalog?.runners.find(item => item.id === runner)
  return <div className="refresh-action-container space-y-2 rounded-lg border border-line bg-raised/50 p-3 text-xs">
    <div className="flex items-start justify-between gap-3">
      <p role="status" className={state === 'detected' && !loading && !error ? 'text-brand' : 'text-muted'}>{loading ? t('ui.runnerStatus.checking') : error ? t('ui.runnerStatus.failed', { error }) : t('ui.runnerStatus.' + state)}</p>
      <RefreshButton size="sm" variant="ghost" label={t('ui.runnerStatus.refresh')} disabled={loading} onClick={refresh} />
    </div>
    {!loading && !error && state === 'detected' && <p className="text-muted">{t('ui.runnerStatus.authentication')}</p>}
    {!loading && !error && state === 'missing' && entry && <details>
      <summary className="cursor-pointer text-muted">{t('ui.runnerStatus.installHelp')}</summary>
      <div className="mt-2 space-y-2"><code className="block overflow-x-auto whitespace-nowrap rounded bg-canvas p-2">{entry.install}</code><div className="flex flex-wrap items-center gap-3"><Button size="sm" onClick={async () => { try { const success = await copyText(entry.install); setCopied(success); setCopyError(!success) } catch { setCopyError(true) } }}>{t(copied ? 'ui.agents.copied' : 'ui.agents.copyInstall')}</Button><a href={entry.docs_url} target="_blank" rel="noreferrer" className="text-brand underline underline-offset-2">{t('ui.runnerStatus.docs')}</a></div>{copyError && <p role="alert" className="text-danger">{t('ui.agents.copyFailed')}</p>}</div>
    </details>}
    {!loading && !error && state !== 'detected' && <p className="text-muted">{t('ui.runnerStatus.portable')}</p>}
  </div>
}
