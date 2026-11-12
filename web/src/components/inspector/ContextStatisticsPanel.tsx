import { getLocale, t, useLocale } from '@/i18n'
import { Gauge, ListTodo, Wrench } from 'lucide-react'
import { cacheMetrics } from '@/lib/promptCache'
import type { ContextStatistics } from '@/lib/protocol.generated'
import { cn, formatTokens } from '@/lib/utils'
import { Section } from '@/components/ui/primitives'

export function ContextStatisticsPanel({ statistics }: { statistics: ContextStatistics }) {
  useLocale()

  const usage = statistics.usage
  const computed = cacheMetrics(usage)
  const invalid = computed.invalid || statistics.cache_inconsistent_requests > 0
  const metrics = { ...computed, invalid, hitRate: invalid ? null : computed.hitRate }
  const reasoning = Number(usage.reasoning_tokens ?? 0)
  const rows: Array<[string, number, string]> = [
    [t("ui.components.inspector.Inspector.2894f16ac7c7"), metrics.input, 'text-brand'],
    [t("ui.components.inspector.Inspector.1ded6559f626"), metrics.read, 'text-ok'],
    [t("ui.components.inspector.Inspector.3a85dbcbf01c"), metrics.output, 'text-muted'],
    [t("ui.components.inspector.Inspector.53fd5dc67cce"), reasoning, 'text-muted'],
  ]
  return <section aria-label={t('ui.context.review.summary')} className="space-y-5">
    {statistics.tree_cost && <Section title={t('ui.context.statistics.treeTitle')}>
      <UsageTotal value={statistics.tree_cost.usage.total_tokens} />
      <p className="mt-1 text-2xs leading-relaxed text-faint">{t('ui.context.statistics.treeSummary', {
        sessions: formatTokens(statistics.tree_cost.session_count), requests: formatTokens(statistics.tree_cost.requests),
      })}</p>
      {statistics.tree_cost.unreported_requests > 0 ? <p role="status" className="mt-2 text-xs text-warn">{t('ui.context.statistics.treeMissing', {count: formatTokens(statistics.tree_cost.unreported_requests)})}</p> : null}
    </Section>}
    {!statistics.tree_cost && <Section title={t('ui.context.statistics.total')}>
      <UsageTotal value={metrics.total} />
      <p className="mt-1 text-2xs leading-relaxed text-faint">{t("ui.components.inspector.Inspector.ef3c2845b8b0")}</p>
    </Section>}
    {statistics.unreported_requests > 0 && (!statistics.tree_cost || statistics.tree_cost.unreported_requests !== statistics.unreported_requests) ? <p role="status" className="text-xs text-warn">{t('ui.context.statistics.currentMissing', {count: formatTokens(statistics.unreported_requests)})}</p> : null}
    {metrics.invalid ? <p role="status" className="text-xs text-warn">{t('ui.context.statistics.invalidCache')}</p> : null}
    <details className="rounded-lg border border-line p-3"><summary className="cursor-pointer text-xs font-medium">{t('ui.context.statistics.details')}</summary>
    <div className="mt-4 space-y-5">
    {statistics.tree_cost ? <Section title={t('ui.context.statistics.total')}><UsageTotal value={metrics.total} /></Section> : null}
    {statistics.tree_cost ? <p className="text-2xs leading-relaxed text-faint">{t('ui.context.statistics.treeBytes', {bytes: formatTokens(statistics.tree_cost.body_bytes)})}</p> : null}
    {statistics.tree_cost && statistics.tree_cost.compaction_requests > 0 ? <p className="text-2xs text-faint">{t('ui.context.statistics.treeCompactions', {count: formatTokens(statistics.tree_cost.compaction_requests)})}</p> : null}
    <Section title={t("ui.components.inspector.Inspector.842efdb5707b")}>
      <div className="flex items-center gap-4 rounded-xl border border-line bg-raised/40 p-3">
        <Ring percent={metrics.hitRate ?? 0} warn={false} />
        <div className="min-w-0 flex-1 space-y-1">
          <span className="text-lg font-semibold tabular-nums text-ink">{metrics.hitRate === null ? t("ui.components.inspector.Inspector.b4724185c9ed") : `${(metrics.hitRate * 100).toFixed(1)}%`}</span>
          {!metrics.invalid ? <p className="text-2xs leading-relaxed text-faint">{metrics.hitRate === null ? t('ui.components.inspector.Inspector.a4e23161f46c') : metrics.coverage !== null && metrics.coverage < 1 && metrics.reported > 0 ? t('ui.context.statistics.cacheCoverage', { reported: formatTokens(metrics.reported), input: formatTokens(metrics.input) }) : t('ui.components.inspector.Inspector.28582c90458d')}</p> : null}
        </div>
      </div>
    </Section>
    <Section title={t('ui.context.statistics.breakdown')}>
      <ul className="space-y-1.5">{rows.map(([label, value, tone]) => <li key={label} className="flex items-center gap-2 text-2xs">
        <span className={cn('h-1.5 w-1.5 rounded-full bg-current', tone)} /><span className="w-16 text-muted">{label}</span>
        <div className="h-1 flex-1 overflow-hidden rounded-full bg-raised"><div className={cn('h-full rounded-full bg-current', tone)} style={{ width: `${metrics.total ? Math.min(100, value / metrics.total * 100) : 0}%` }} /></div>
        <span className="shrink-0 text-right tabular-nums text-faint">{formatTokens(value)}</span>
      </li>)}</ul>
    </Section>
    <Section title={t("ui.components.inspector.Inspector.62873083fcae")}><div className="grid grid-cols-2 gap-1.5">
      <Stat icon={<Gauge className="h-3 w-3" />} label={t('ui.context.statistics.requests')} value={formatTokens(statistics.requests)} />
      <Stat icon={<Gauge className="h-3 w-3" />} label={t("ui.context.statistics.reportedRequests")} value={formatTokens(statistics.reported_requests)} />
      <Stat icon={<Wrench className="h-3 w-3" />} label={t("ui.components.inspector.Inspector.a8ca3c13eb75")} value={formatTokens(statistics.tool_calls)} />
      <Stat icon={<Wrench className="h-3 w-3" />} label={t("ui.components.inspector.Inspector.a6c975c0574a")} value={formatTokens(statistics.tool_errors)} tone={statistics.tool_errors ? 'danger' : undefined} />
      <Stat icon={<ListTodo className="h-3 w-3" />} label={t('ui.context.statistics.subagents')} value={statistics.subagents === null ? '—' : formatTokens(statistics.subagents)} />
    </div></Section>
    {statistics.compaction_requests > 0 ? <p className="text-2xs text-faint">{t('ui.context.statistics.currentCompactions', {count: formatTokens(statistics.compaction_requests)})}</p> : null}
    </div></details>
  </section>
}

function UsageTotal({ value }: { value: number }) {
  const locale = getLocale()
  const units: Array<[number, string]> = locale === 'zh-CN'
    ? [[100_000_000, 'ui.context.statistics.compactHundredMillion'], [1_000_000, 'ui.context.statistics.compactMillion'], [1_000, 'ui.context.statistics.compactThousand']]
    : [[1_000_000_000, 'ui.context.statistics.compactBillion'], [1_000_000, 'ui.context.statistics.compactMillion'], [1_000, 'ui.context.statistics.compactThousand']]
  const unit = units.find(([threshold]) => value >= threshold)
  const compact = unit ? t(unit[1], { count: (value / unit[0]).toLocaleString(locale, { maximumFractionDigits: 1 }) }) : null
  return <div className="flex flex-wrap items-baseline gap-x-2 tabular-nums text-ink">
    {compact ? <span className="text-lg font-semibold">{compact}</span> : null}
    <span className={compact ? 'text-xs text-muted' : 'text-lg font-semibold'}>{formatTokens(value)}</span>
  </div>
}

function Ring({ percent, warn }: { percent: number; warn: boolean }) {
  useLocale()

  const size = 64
  const stroke = 6
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0 -rotate-90">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} className="stroke-line" />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={c}
        strokeDashoffset={c * (1 - percent)}
        className={cn('transition-stroke-offset duration-700 ease-swift', warn ? 'stroke-danger' : 'stroke-brand')}
      />
    </svg>
  )
}

function Stat({
  icon,
  label,
  value,
  tone,
}: {
  icon: React.ReactNode
  label: string
  value: string
  tone?: string
}) {
  useLocale()

  return (
    <div className="rounded-lg border border-line bg-raised/40 px-2 py-1.5">
      <div className="flex items-center gap-1 text-2xs text-faint">
        {icon}
        {label}
      </div>
      <div className={cn('mt-0.5 text-sm font-semibold tabular-nums text-ink', tone)}>{value}</div>
    </div>
  )
}
