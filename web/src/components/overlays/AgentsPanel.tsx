import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { useCallback, useEffect, useState } from 'react'
import { ArrowUpRight, Check, Copy, MessageSquare, Terminal, Users, Workflow } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { api } from '@/lib/api'
import { externalAgentCommand, type ExternalAgentCatalog } from '@/lib/externalAgents'
import { copyText } from '@/lib/utils'
import { Button, RefreshButton } from '@/components/ui/primitives'
import { useAppStore } from '@/store/app'
import { useDraftStore } from '@/store/drafts'
import { invocationDraft } from '@/lib/invocationDraft'

export function AgentsPanel({ onNavigate, externalOnly = false }: { onNavigate: (target: 'roles' | 'teams' | 'worktrees') => void; externalOnly?: boolean }) {
  useLocale()
  const sessionId = useAppStore(state => state.activeSessionId)
  const [catalog, setCatalog] = useState<ExternalAgentCatalog | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<ResourceText>('')
  const [copied, setCopied] = useState('')
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    void api.externalAgents(controller.signal).then(result => {
      if (controller.signal.aborted) return
      if (result.ok) setCatalog(result.data)
      else setError(result.copy ?? result.message)
      setLoading(false)
    })
    return () => controller.abort()
  }, [revision])

  useEffect(() => {
    if (!copied) return
    const timer = setTimeout(() => setCopied(''), 2000)
    return () => clearTimeout(timer)
  }, [copied])

  const copy = useCallback(async (key: string, text: string) => {
    try { if (!await copyText(text)) throw new Error('clipboard'); setCopied(key); setError('') }
    catch { setError({key: 'ui.agents.copyFailed'}) }
  }, [])

  return <div className="space-y-6">
    {!externalOnly && <><header className="space-y-2">
      <h3 className="ui-section-title">{t('ui.agents.title')}</h3>
      <p className="max-w-prose text-xs leading-relaxed text-muted">{t('ui.agents.description')}</p>
    </header>

    <section aria-label={t('ui.agents.rolesTitle')} className="divide-y divide-line rounded-xl bg-raised/60 px-4">
      <div className="flex items-start gap-3 py-4">
        <Users aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
        <div className="min-w-0 flex-1 space-y-1">
          <h4 className="text-xs font-semibold text-ink">{t('ui.agents.rolesTitle')}</h4>
          <p className="text-xs leading-relaxed text-muted">{t('ui.agents.rolesHint')}</p>
          <Button size="sm" variant="ghost" className="-ml-2.5 mt-1" onClick={() => onNavigate('roles')} iconRight={<ArrowUpRight className="h-3 w-3" />}>{t('ui.agents.rolesAction')}</Button>
        </div>
      </div>
      <div className="flex items-start gap-3 py-4">
        <Workflow aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
        <div className="min-w-0 flex-1 space-y-1">
          <h4 className="text-xs font-semibold text-ink">AgentTeams</h4>
          <p className="text-xs leading-relaxed text-muted">{t('ui.agents.teamsHint')}</p>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            <Button size="sm" variant="ghost" className="-ml-2.5 mt-1" onClick={() => onNavigate('teams')} iconRight={<ArrowUpRight className="h-3 w-3" />}>{t('ui.agents.teamsAction')}</Button>
            <Button size="sm" variant="ghost" className="mt-1" onClick={() => onNavigate('worktrees')}>{t('ui.agents.worktreesAction')}</Button>
          </div>
        </div>
      </div>
    </section></>}

    <section className="refresh-action-container space-y-3" aria-labelledby="external-agents-title">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Terminal aria-hidden className="h-4 w-4 text-muted" />
          <h4 id="external-agents-title" className="ui-section-title">{t('ui.agents.externalTitle')}</h4>
        </div>
        <RefreshButton size="sm" label={t('ui.agents.refresh')} disabled={loading} spinning={loading} onClick={() => setRevision(value => value + 1)} />
      </div>
      <p className="text-xs leading-relaxed text-muted">{t('ui.agents.externalHint')}</p>
      {error && <p role="alert" className="text-xs leading-relaxed text-danger [overflow-wrap:anywhere]">{noticeText(error)}</p>}
      {loading && !catalog ? <div role="status" aria-label={t('ui.agents.detecting')} className="space-y-3">{[0, 1, 2].map(key => <div key={key} aria-hidden className="h-24 animate-pulse rounded-lg bg-raised motion-reduce:animate-none" />)}</div> : null}
      {catalog && <div className="divide-y divide-line border-y border-line" aria-busy={loading}>
        {[...catalog.runners].sort((a, b) => Number(b.available) - Number(a.available) || Number(!!b.cli_path) - Number(!!a.cli_path)).map(runner => <article key={runner.id} className="space-y-2.5 py-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h5 className="text-sm font-medium text-ink">{runner.title}</h5>
            <span className={'inline-flex items-center gap-1.5 text-2xs ' + (runner.available ? 'text-brand' : 'text-muted')}>
              {runner.available && <Check aria-hidden className="h-3 w-3" />}
              {t(runner.available ? 'ui.agents.available' : runner.cli_path ? 'ui.agents.adapterMissing' : 'ui.agents.notInstalled')}
            </span>
          </div>
          <code className="block overflow-x-auto whitespace-nowrap text-2xs text-faint" title={runner.command_path ?? undefined}>{runner.command_path ?? runner.command}{runner.args.length ? ' ' + runner.args.join(' ') : ''}</code>
          <div className="flex flex-wrap items-center gap-2">
            {runner.available && catalog.delegation_enabled && <Button size="sm" variant="primary" disabled={!sessionId || loading} icon={<MessageSquare className="h-3 w-3" />} onClick={() => {
              const currentSession = useAppStore.getState().activeSessionId
              if (!currentSession) return
              const drafts = useDraftStore.getState()
              drafts.setText(currentSession, invocationDraft('runner', runner.id, drafts.bySession[currentSession]?.text ?? ''))
              useAppStore.getState().setSettingsOpen(false)
            }}>{t('ui.agents.useInChat')}</Button>}
            <Button size="sm" variant="ghost" onClick={() => void copy('run:' + runner.id, externalAgentCommand(runner.id))} icon={copied === 'run:' + runner.id ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}>{t(copied === 'run:' + runner.id ? 'ui.agents.copied' : 'ui.agents.copyRun')}</Button>
            {!runner.available && <Button size="sm" variant="ghost" onClick={() => void copy('install:' + runner.id, runner.install)}>{t(copied === 'install:' + runner.id ? 'ui.agents.copied' : 'ui.agents.copyInstall')}</Button>}
            <a href={runner.docs_url} target="_blank" rel="noreferrer" className="inline-flex min-h-8 items-center gap-1 rounded px-2 text-xs text-muted hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand">{t('ui.agents.docs')}<ArrowUpRight aria-hidden className="h-3 w-3" /></a>
          </div>
        </article>)}
      </div>}
      <p className="text-2xs leading-relaxed text-faint">{t('ui.agents.runHint')}</p>
      {!sessionId && <p className="text-2xs leading-relaxed text-faint">{t('ui.agents.selectChat')}</p>}
      {catalog && !catalog.delegation_enabled && <p className="text-2xs leading-relaxed text-faint">{t('ui.agents.delegationDisabled')}</p>}
      <p className="text-2xs leading-relaxed text-faint">{t('ui.agents.authHint')}</p>
      <span role="status" className="sr-only">{copied ? t('ui.agents.copied') : ''}</span>
    </section>
  </div>
}
