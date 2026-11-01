import {t, useLocale} from '@/i18n'
import {useAppStore} from '@/store/app'
import type {TurnCompletion} from '@/lib/turnCompletion'
import {Button} from '@/components/ui/primitives'
import {artifactPreviewTarget} from '@/lib/turnDelivery'

/** 摘要仅呈现持久事实，验证状态不由工具成功次数推导。 */
export function TurnResults({data, sessionId, onRefresh, loading, readFailed = false}: {data: TurnCompletion; sessionId: string; onRefresh: () => void; loading: boolean; readFailed?: boolean}) {
  useLocale()
  const delivery = data.delivery
  const current = useAppStore(s => s.activeSessionId === sessionId)
  const confirmed = !loading && !readFailed
  const artifacts = delivery.artifacts.map(artifact => ({artifact, target: artifactPreviewTarget(artifact)}))
  const checks = delivery.checks.length ? <ul className="mt-2 space-y-1">{delivery.checks.map(check => <li key={check.source_seq} className="break-words">{check.check_id}{' · '}{t('ui.delivery.check.' + check.status)}<span className="ml-2 text-faint">{check.paths.join(', ')}</span></li>)}</ul> : null
  return <details className="mb-2 rounded-xl border border-line px-3 py-2" data-turn-results={data.turn}>
    <summary className="cursor-pointer text-xs font-medium text-muted">{t('ui.delivery.title')}</summary>
    <dl className="mt-3 space-y-3 text-xs leading-reading">
      <div><dt className="font-medium text-ink">{t('ui.delivery.work')}</dt><dd className="mt-1 text-muted">{t('ui.delivery.tools', {count: delivery.tool_executions})}{delivery.artifact_count > 0 ? ' ' + t('ui.delivery.artifacts', {count: delivery.artifact_count}) : ''}</dd>
        {artifacts.length ? <dd><ul className="mt-2 space-y-2">{artifacts.map(({artifact, target}) => <li key={artifact.path} className="min-w-0">
          <button type="button" disabled={!current || !target} title={artifact.path} aria-label={t('ui.delivery.openArtifact', {path: artifact.path})}
            className="max-w-full break-all rounded text-left text-brand underline decoration-brand/40 underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand disabled:text-muted disabled:no-underline"
            onClick={() => {
              const app = useAppStore.getState()
              if (target && app.activeSessionId === sessionId) app.revealInFiles(target.path, undefined, {root: target.root})
            }}>{target?.path ?? artifact.path}</button>
          {artifact.description ? <p className="mt-0.5 break-words text-muted">{artifact.description}</p> : null}
        </li>)}</ul></dd> : null}
        {delivery.artifact_count > artifacts.length ? <dd className="mt-1 text-faint">{t('ui.delivery.artifactsLimited', {shown: artifacts.length, total: delivery.artifact_count})}</dd> : null}
        {artifacts.some(row => !row.target) ? <dd className="mt-1 text-faint">{t('ui.delivery.artifactUnbound')}</dd> : null}
        {artifacts.length ? <dd className="mt-1 text-faint">{t('ui.delivery.artifactCurrent')}</dd> : null}
      </div>
      <div><dt className="font-medium text-ink">{t('ui.delivery.validation')}</dt><dd role="status" data-verification-state={confirmed ? delivery.verification : loading ? 'checking' : 'unknown'} className="mt-1 text-muted">{t(confirmed ? 'ui.delivery.' + delivery.verification : loading ? 'ui.delivery.checking' : 'ui.delivery.readUnconfirmed')}</dd>
        <dd className="mt-1 text-faint">{t('ui.delivery.readSnapshot')}</dd>
        {checks ? <dd>{confirmed ? checks : <details className="mt-2 text-muted"><summary className="cursor-pointer">{t('ui.delivery.historicalChecks')}</summary>{checks}</details>}</dd> : null}
      </div>
      <div><dt className="font-medium text-ink">{t('ui.delivery.remaining')}</dt><dd className="mt-1 text-muted">{t(delivery.failed_tools || delivery.unknown_tools ? 'ui.delivery.failures' : 'ui.delivery.noFailures', {failed: delivery.failed_tools, unknown: delivery.unknown_tools})}</dd></div>
    </dl>
    <div className="mt-3 flex flex-wrap gap-2" aria-label={t('ui.delivery.next')}>
      <Button size="sm" disabled={loading} onClick={onRefresh}>{t('ui.delivery.recheck')}</Button>
      <Button size="sm" disabled={!current} onClick={() => {
        const app = useAppStore.getState()
        if (app.activeSessionId === sessionId) app.revealTurnChanges(sessionId, data.turn)
      }}>{t('ui.delivery.changes')}</Button>
      <Button size="sm" disabled={!current} onClick={() => {
        const app = useAppStore.getState()
        if (app.activeSessionId === sessionId) app.revealRun(sessionId, {turn: data.turn})
      }}>{t('ui.delivery.execution')}</Button>
    </div>
  </details>
}
