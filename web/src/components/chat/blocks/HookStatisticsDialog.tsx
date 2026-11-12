import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale } from '@/i18n'
import { useId } from 'react'
import { X } from 'lucide-react'
import { Overlay } from '@/components/ui/primitives'
import { formatDuration } from '@/lib/utils'
import type { TurnCompletion } from '@/lib/turnCompletion'
import { HookActivityRecords } from './HookActivity'

export function HookStatisticsDialog({ data, sessionId, loading, error, onRetry, onPage, onClose }: {
  data: TurnCompletion | null; sessionId: string | null; loading: boolean; error: ResourceText
  onRetry: () => void; onPage: (offset: number) => void; onClose: () => void
}) {
  useLocale()

  const heading = useId(), hooks = data?.hooks
  return <Overlay onClose={onClose} labelledBy={heading} className="flex max-h-dialog-wide max-w-2xl flex-col">
    <header className="flex shrink-0 items-center justify-between px-5 pb-4 pt-5 sm:px-7 sm:pt-6">
      <h2 id={heading} className="text-xl font-semibold tracking-tight text-ink">{t("ui.components.chat.blocks.HookStatisticsDialog.909ee3161e8a")}</h2>
      <button type="button" aria-label={t("ui.components.chat.blocks.HookStatisticsDialog.11b7ae2f47bd")} onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-lg text-muted hover:bg-raised hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"><X aria-hidden className="h-4 w-4" /></button>
    </header>
    <div aria-busy={loading} className="min-h-0 flex-1 overflow-y-auto px-5 pb-5 sm:px-7">
      {error ? <div role="alert" className="mb-3 space-y-2 text-sm text-danger"><p>{noticeText(error)}</p><button type="button" onClick={onRetry} className="underline">{t("ui.components.chat.blocks.HookStatisticsDialog.df7392cc96be")}</button></div> : null}
      {!hooks ? <p role="status" className="text-sm text-muted">{error ? t("ui.components.chat.blocks.HookStatisticsDialog.3ffe121e7a15") : t("ui.components.chat.blocks.HookStatisticsDialog.dff520653119")}</p> : <>
        <dl className="space-y-2 text-sm text-muted">
          <div className="flex justify-between gap-6"><dt title={t("ui.components.chat.blocks.HookStatisticsDialog.69ecb1bea7b9")}>{t("ui.components.chat.blocks.HookStatisticsDialog.053641ca4902")}</dt><dd className="tabular-nums text-ink">{hooks.runs}</dd></div>
          <div className="flex justify-between gap-6"><dt title={t("ui.components.chat.blocks.HookStatisticsDialog.46d79fc7d330")}>{t("ui.components.chat.blocks.HookStatisticsDialog.399bb82e282c")}</dt><dd className="tabular-nums text-ink">{hooks.blocked}</dd></div>
          <div className="flex justify-between gap-6"><dt title={t("ui.components.chat.blocks.HookStatisticsDialog.d97723f906f3")}>{t("ui.components.chat.blocks.HookStatisticsDialog.af0c4b935b8d")}</dt><dd className="tabular-nums text-ink">{hooks.unsuccessful}</dd></div>
        </dl>
        <p className="mt-4 text-xs text-faint">{t(hooks.counts.skipped ? 'ui.hookStatistics.summarySkipped' : 'ui.hookStatistics.summary', {turn: data!.turn, duration: formatDuration(hooks.duration_ms), skipped: hooks.counts.skipped ?? 0})}</p>
        <h3 className="mb-3 mt-6 text-sm font-medium text-muted">{t("ui.components.chat.blocks.HookStatisticsDialog.6e55e0f207bf")}</h3>
        {!hooks.total ? <p className="py-3 text-sm text-faint">{t("ui.components.chat.blocks.HookStatisticsDialog.3c8409847e5d")}</p> : <HookActivityRecords key={hooks.offset} rows={hooks.rows} sessionId={sessionId} />}
      </>}
    </div>
    {hooks && hooks.total > 50 ? <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-line px-5 py-3 text-xs text-muted sm:px-7">
      <span>{t('ui.hookStatistics.pagination', {start: hooks.offset + 1, end: hooks.offset + hooks.rows.length, total: hooks.total})}</span>
      <div className="flex gap-3"><button type="button" disabled={loading || hooks.offset === 0} className="text-brand disabled:text-faint" onClick={() => onPage(Math.max(0, hooks.offset - 50))}>{t("ui.components.chat.blocks.HookStatisticsDialog.c9b9ae7a6144")}</button>
        <button type="button" disabled={loading || hooks.next_offset === null} className="text-brand disabled:text-faint" onClick={() => onPage(hooks.next_offset!)}>{t("ui.components.chat.blocks.HookStatisticsDialog.8a8542f69648")}</button></div>
    </footer> : null}
  </Overlay>
}
