import {t, useLocale} from '@/i18n'
import type {RecoveryPreview} from '@/lib/protocol.generated'

/** 只呈现服务端完整运行的事实，不能将已载入页面当作全部执行。 */
export function RecoverySummary({preview}: {preview: RecoveryPreview}) {
  useLocale()
  return <section data-recovery-summary aria-label={t('ui.recovery.summaryTitle')} className="space-y-2 rounded-lg border border-line bg-raised/50 p-3 text-xs leading-reading">
    <h3 className="ui-group-title text-ink">{t('ui.recovery.summaryTitle')}</h3>
    <p className={preview.verification_required ? 'text-warn' : 'text-muted'}>{t(!preview.scope_known ? 'ui.recovery.scopeUnknown'
      : preview.uncertain_operations ? 'ui.recovery.pendingOperations' : 'ui.recovery.noPendingOperations', {count: preview.uncertain_operations})}</p>
    {preview.scope_known && preview.total_tools > 0 ? <p className="text-muted">{t('ui.recovery.loadedRecords', {loaded: preview.tools.length, total: preview.total_tools})}</p> : null}
    {preview.next_offset != null ? <p className="text-muted">{t('ui.recovery.moreRecords')}</p> : null}
    <p className="text-faint">{t('ui.recovery.evidenceLimit')}</p>
  </section>
}
