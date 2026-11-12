import { usePageVisible } from '@/hooks/usePageVisible'
import { t, useLocale , getLocale } from '@/i18n'
import { memo, useEffect, useState } from 'react'
import { ChevronRight, CircleDot } from 'lucide-react'
import { generationLabel, generationTime, generationHealthTime, secondsLabel, type GenerationState } from '@/lib/generation'
import { cn } from '@/lib/utils'
import { noticeText } from '@/lib/noticeText'

export const GenerationDock = memo(function GenerationDock({ generation, connection, stopping }: {
  generation: GenerationState | null
  connection: string
  stopping: boolean
}) {
  useLocale()

  const visible = usePageVisible()
  const [now, setNow] = useState(() => performance.now())
  const connected = connection === 'open'
  useEffect(() => {
    setNow(performance.now())
    if (!generation || !connected || !visible) return
    const timer = setInterval(() => setNow(performance.now()), 1000)
    return () => clearInterval(timer)
  }, [generation?.request_id, connected, visible])
  if (!generation) return null
  const clock = generationTime(generation, Math.max(now, generation.receivedAt))
  const health = generationHealthTime(generation, Math.max(now, generation.receivedAt))
  const retryWait = Math.max(0, generation.retry_in_seconds * 1000 - clock.elapsed_ms)
  const waiting = generation.phase === 'retrying'
  const quiet = connected && !stopping && !waiting && clock.idle_ms >= 20_000
  const modelLabel = t(connected ? 'ui.generation.requestModel' : 'ui.generation.lastRequestModel',
    {model: generation.model || t('ui.components.chat.GenerationDock.e403ba5798ba')})
  return <details className="composer-generation group/generation min-w-0 rounded-lg text-xs" aria-label={t("ui.components.chat.GenerationDock.859809cd4641")}>
    <summary title={modelLabel} className="flex min-h-8 cursor-pointer list-none items-center gap-2 rounded-lg px-2 py-1 text-muted hover:bg-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand">
      <CircleDot className={cn('h-3.5 w-3.5 shrink-0', !connected || quiet ? 'text-warn' : 'text-brand')} aria-hidden="true" />
      <span className={cn('min-w-0 flex-1 truncate font-medium', (!connected || quiet || waiting) && 'text-warn')} aria-live="polite">{generationLabel(generation, connected, stopping)}</span>
      <span className="shrink-0 tabular-nums text-muted">{secondsLabel(clock.elapsed_ms)}</span>
      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted group-open/generation:rotate-90" aria-hidden="true" />
    </summary>
    <div className="space-y-2 border-t border-line/70 px-3 py-2 text-muted">
      <dl className="grid grid-cols-detail gap-x-3 gap-y-1.5">
        <dt>{t("ui.components.chat.GenerationDock.c98e118e0a43")}</dt><dd className="break-all text-ink">{generation.model || t("ui.components.chat.GenerationDock.e403ba5798ba")}</dd>
        <dt>{t("ui.components.chat.GenerationDock.0e4138ec12b5")}</dt><dd>{generation.attempt == null ? t("ui.components.chat.GenerationDock.b2ff4e9c1e2b") : `${generation.attempt} / ${generation.max_attempts ?? generation.attempt}`}</dd>
        {generation.network_timeout_seconds != null ? <><dt>{t("ui.components.chat.GenerationDock.5504469cb8af")}</dt><dd>{t("ui.generation.networkTimeout", {duration: secondsLabel(generation.network_timeout_seconds * 1000)})}</dd>
          <dt>{t("ui.components.chat.GenerationDock.ad0e3cfab370")}</dt><dd>{t(generation.network_bytes ? "ui.generation.networkReceived" : "ui.generation.networkWaiting", {bytes: (generation.network_bytes ?? 0).toLocaleString(getLocale()), duration: secondsLabel(health.network_idle_ms)})}</dd></> : null}
        <dt>{t("ui.components.chat.GenerationDock.9681958f071a")}</dt><dd className="tabular-nums">{t("ui.metrics.characters", {count: generation.text_chars.toLocaleString(getLocale())})}</dd>
        <dt>{t("ui.components.chat.GenerationDock.532a343c8159")}</dt><dd className="tabular-nums">{t("ui.metrics.characters", {count: generation.thinking_chars.toLocaleString(getLocale())})}</dd>
        {generation.context_window != null ? <><dt>{t("ui.components.chat.GenerationDock.2d1845d307fc")}</dt><dd>{t("ui.metrics.tokens", {count: generation.context_window.toLocaleString(getLocale())})}</dd></> : null}
        {generation.requested_max_tokens != null ? <><dt>{t("ui.components.chat.GenerationDock.d65b027a6d18")}</dt><dd>{t("ui.metrics.tokens", {count: generation.requested_max_tokens.toLocaleString(getLocale())})}</dd></> : null}
        {generation.max_tokens != null ? <><dt>{t("ui.components.chat.GenerationDock.90ddd27e5216")}</dt><dd>{t("ui.metrics.tokens", {count: generation.max_tokens.toLocaleString(getLocale())})}</dd></> : null}
      </dl>
      {generation.adjustments?.map((reason, index) => <p key={index}>{noticeText(reason)}</p>)}
      {waiting && connected && !stopping ? <p>{retryWait > 0 ? t("ui.components.chat.GenerationDock.61e823e06a8c", {p0: (secondsLabel(Math.ceil(retryWait / 1000) * 1000))}) : t("ui.components.chat.GenerationDock.d0e30898532d")}</p> : null}
      {quiet ? <p className="text-warn">{t("ui.generation.quietWaiting", {idle: secondsLabel(clock.idle_ms)})}</p> : null}
      {!connected ? <p>{t("ui.components.chat.GenerationDock.1bc9620c1772")}</p> : null}
      <p className="text-2xs text-faint">{t("ui.components.chat.GenerationDock.22c5bc2db1da")}</p>
    </div>
  </details>
})
