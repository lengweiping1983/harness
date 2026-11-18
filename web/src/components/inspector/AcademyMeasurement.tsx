import {t, useLocale} from '@/i18n'
import type {AcademyMastery} from '@/types'
import {academyEfficacyMetrics, academyExportEvidence} from '@/lib/academy'
import {Button} from '@/components/ui/primitives'

/** 学习效果实测仪表：把证据账本聚合为实测协议口径的指标（延迟保持/迁移/修复/效率），附严谨协议与诚实 caveat；观察性证据，非对照试验。 */
export function AcademyMeasurement({mastery}: {mastery: AcademyMastery}) {
  useLocale()
  const metrics = academyEfficacyMetrics(mastery)
  const pct = (value: number | null) => value == null ? '—' : value + '%'
  return <div data-academy-measurement className="space-y-2 rounded-xl border border-line p-3">
    <p className="text-xs font-medium">{t('ui.academy.measurement.title')}</p>
    <p className="text-xs leading-reading text-muted">{t('ui.academy.measurement.caveat')}</p>
    <div className="grid gap-2 sm:grid-cols-3">
      <div data-measurement-retention className="rounded-lg border border-line p-2">
        <p className="text-lg font-semibold">{pct(metrics.delayedRate)}</p>
        <p className="text-xs text-muted">{t('ui.academy.measurement.retention')} · {metrics.delayedCorrect}/{metrics.delayedAttempts}</p>
      </div>
      <div data-measurement-transfer className="rounded-lg border border-line p-2">
        <p className="text-lg font-semibold">{pct(metrics.transferRate)}</p>
        <p className="text-xs text-muted">{t('ui.academy.measurement.transfer')} · {metrics.transfers}/{metrics.transferAttempts}</p>
      </div>
      <div data-measurement-repair className="rounded-lg border border-line p-2">
        <p className="text-lg font-semibold">{pct(metrics.repairRate)}</p>
        <p className="text-xs text-muted">{t('ui.academy.measurement.repair')} · {metrics.repairs}/{metrics.repairs + metrics.blindspotsOpen}</p>
      </div>
      <div data-measurement-efficiency className="rounded-lg border border-line p-2">
        <p className="text-lg font-semibold">{metrics.minutesPerDurable == null ? '—' : metrics.minutesPerDurable + ' ' + t('ui.academy.measurement.efficiencyUnit')}</p>
        <p className="text-xs text-muted">{t('ui.academy.measurement.efficiency')}</p>
      </div>
      <div data-measurement-mastery className="rounded-lg border border-line p-2">
        <p className="text-lg font-semibold">{metrics.durable}/{metrics.durable + metrics.learning + metrics.fresh}</p>
        <p className="text-xs text-muted">{t('ui.academy.measurement.mastery')}</p>
      </div>
      <div data-measurement-focus className="rounded-lg border border-line p-2">
        <p className="text-lg font-semibold">{metrics.totalFocusMinutes} {t('ui.academy.measurement.efficiencyUnit')}</p>
        <p className="text-xs text-muted">{t('ui.academy.measurement.totalFocus')}</p>
      </div>
    </div>
    {!metrics.hasDelayedEvidence ? <p className="text-xs leading-reading text-warn">{t('ui.academy.measurement.noDelayed')}</p> : null}
    {metrics.minutesPerDurable == null ? <p className="text-xs text-muted">{t('ui.academy.measurement.noDurable')}</p> : null}
    <details className="rounded-lg border border-line p-2 text-xs">
      <summary className="cursor-pointer font-medium text-muted">{t('ui.academy.measurement.protocolTitle')}</summary>
      <p className="mt-1 leading-reading text-muted">{t('ui.academy.measurement.protocol')}</p>
    </details>
    <Button size="xs" variant="ghost" data-measurement-export onClick={() => academyExportEvidence(mastery)}>{t('ui.academy.report.export')}</Button>
  </div>
}
