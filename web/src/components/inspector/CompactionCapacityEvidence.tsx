import { t, useLocale } from '@/i18n'
import type { CompactionAudit, CompactionCapacity } from '@/lib/contextAudit'
import { formatNumber } from '@/lib/utils'

const fields: [keyof Omit<CompactionCapacity, 'version'>, string][] = [
  ['message_tokens', 'ui.components.inspector.CompactionCapacityEvidence.a9882de5cd3a'], ['runtime_tokens', 'ui.components.inspector.CompactionCapacityEvidence.381da5690a2e'], ['tool_tokens', 'ui.context.category.tools'],
  ['input_tokens', 'ui.components.inspector.CompactionCapacityEvidence.3efc8c8c6385'], ['context_window', 'ui.components.inspector.CompactionCapacityEvidence.acdd83bdc00f'], ['compact_threshold', 'ui.components.inspector.CompactionCapacityEvidence.ee4485e976f2'],
  ['remaining_tokens', 'ui.components.inspector.CompactionCapacityEvidence.1410a27bf4fc'],
]

export function CompactionCapacityEvidence({ row }: { row: CompactionAudit }) {
  useLocale()

  const before = row.capacity_before?.version === 1 ? row.capacity_before : undefined
  const after = row.capacity_after?.version === 1 ? row.capacity_after : undefined
  if (!before && !after) return <p className="mt-1 text-faint">{t("ui.components.inspector.CompactionCapacityEvidence.21e63f78e039")}</p>
  const value = (capacity: CompactionCapacity | undefined, key: keyof CompactionCapacity) => {
    if (!capacity) return t("ui.components.inspector.CompactionCapacityEvidence.7450cc5b16e2")
    const number = capacity[key]
    return typeof number === 'number' ? formatNumber(number) : t("ui.components.inspector.CompactionCapacityEvidence.898f3c720c27")
  }
  return <details className="mt-2 border-t border-line pt-2">
    <summary className="cursor-pointer text-muted">{t("ui.components.inspector.CompactionCapacityEvidence.2ce59b229442")}</summary>
    <p className="mt-2 text-faint">{row.trigger === 'threshold' ? t("ui.components.inspector.CompactionCapacityEvidence.5cb9fd1cb392") : row.trigger === 'forced' ? t("ui.components.inspector.CompactionCapacityEvidence.151d2b036eb4") : t("ui.components.inspector.CompactionCapacityEvidence.5e03893670f6")}</p>
    <table aria-label={t("ui.components.inspector.CompactionCapacityEvidence.786f528ce439")} className="mt-2 w-full text-right tabular-nums">
      <thead className="text-faint"><tr><th className="pb-1 text-left font-normal">{t("ui.components.inspector.CompactionCapacityEvidence.fa57409ece41")}</th><th className="pb-1 font-normal">{t("ui.components.inspector.CompactionCapacityEvidence.07f7146482e3")}</th><th className="pb-1 font-normal">{t("ui.components.inspector.CompactionCapacityEvidence.6bdad7611b97")}</th></tr></thead>
      <tbody>{fields.map(([key, label]) => <tr key={key} className={key === 'input_tokens' ? 'border-y border-line font-medium' : ''}>
        <th scope="row" className="py-1 pr-2 text-left font-normal text-muted">{t(label)}</th><td>{value(before, key)}</td><td>{value(after, key)}</td>
      </tr>)}</tbody>
    </table>
    <p className="mt-2 leading-relaxed text-faint">{t("ui.components.inspector.CompactionCapacityEvidence.73d10deab110")}</p>
  </details>
}
