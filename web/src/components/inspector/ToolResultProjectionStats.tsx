import { t, useLocale, getLocale } from '@/i18n'
import type { ToolResultProjectionSummary } from '@/lib/protocol.generated'

export function ToolResultProjectionStats({ value }: { value?: ToolResultProjectionSummary }) {
  useLocale()
  if (!value || value.results === 0) return null
  return <section className="space-y-1 rounded-lg border border-line p-3" aria-label={t('ui.context.projection.title')}>
    <h4 className="text-xs font-medium">{t('ui.context.projection.title')}</h4>
    <p className="text-xs tabular-nums text-muted">{t('ui.context.projection.count', { count: value.abbreviated_results.toLocaleString(getLocale()) })}</p>
    <p className="text-xs tabular-nums">{value.original_chars.toLocaleString(getLocale())} → {value.model_chars.toLocaleString(getLocale())} {t('ui.context.projection.characters')}</p>
    <p className="text-2xs leading-relaxed text-faint">{t('ui.context.projection.note')}</p>
  </section>
}
