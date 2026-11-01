import { useState } from 'react'
import { CirclePlus } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { Button, Switch as ToggleSwitch } from '@/components/ui/primitives'

export function Switch({
  checked,
  disabled,
  onChange,
  label,
}: {
  checked: boolean
  disabled?: boolean
  onChange: (next: boolean) => void
  label: string
}) {
  useLocale()

  return <ToggleSwitch checked={checked} disabled={disabled} aria-label={label}
    onChange={event => onChange(event.target.checked)} />
}

export const field =
  'ui-field placeholder:text-faint focus:outline-none focus:border-brand/50'

/** 安装/新建的折叠表单外壳。 */
export function InstallDisclosure({
  title,
  children,
}: {
  title: string
  children: (close: () => void) => React.ReactNode
}) {
  useLocale()

  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-xl border border-dashed border-line">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-xs text-muted transition-colors hover:text-ink"
      >
        <CirclePlus className="h-3.5 w-3.5" />
        {title}
      </button>
      {open ? (
        <div className="space-y-2 border-t border-line px-3 py-3">
          {children(() => setOpen(false))}
        </div>
      ) : null}
    </div>
  )
}

export function ScopeSelect({
  value,
  onChange,
}: {
  value: 'user' | 'project'
  onChange: (v: 'user' | 'project') => void
}) {
  useLocale()

  return (
    <label className="block space-y-2 text-sm font-medium text-ink">
      {t("ui.scope.label")}
      <select
        className={field}
        value={value}
        onChange={(event) => onChange(event.target.value as 'user' | 'project')}
        aria-label={t("ui.scope.label")}
      >
        <option value="user">{t("ui.scope.user")}</option>
        <option value="project">{t("ui.scope.project")}</option>
      </select>
    </label>
  )
}

export type CatalogScope = 'all' | 'project' | 'shared' | 'bundled'
export function catalogOrigin(source?: string): CatalogScope {
  return source?.startsWith('project') ? 'project' : source === 'bundled' || source === 'system-harness' ? 'bundled' : 'shared'
}
export function matchesCatalog(query: string, values: Array<string | null | undefined>): boolean {
  const text = values.filter(Boolean).join(' ').normalize('NFKC').toLocaleLowerCase()
  return query.normalize('NFKC').toLocaleLowerCase().split(/\s+/).filter(Boolean).every(word => text.includes(word))
}
export function CatalogToolbar({ query, onQuery, scope = 'all', onScope, count, total, label }: { query: string; onQuery: (value: string) => void; scope?: CatalogScope; onScope?: (value: CatalogScope) => void; count: number; total: number; label: string }) {
  useLocale()

  return <div className="space-y-2 rounded-xl border border-line bg-raised/30 p-3">
    <input type="search" className={field} aria-label={label} placeholder={label} value={query} onChange={event => onQuery(event.target.value)} />
    <div className="flex flex-wrap items-center gap-2">
      {onScope && <select className="min-w-0 rounded-lg border border-line bg-canvas px-2 py-1 text-xs text-muted" aria-label={t('ui.esc.search.scope')} value={scope} onChange={event => onScope(event.target.value as CatalogScope)}>{(['all','project','shared','bundled'] as const).map(value => <option key={value} value={value}>{t('ui.esc.search.' + value)}</option>)}</select>}
      {total > 0 ? <span role="status" className="flex-1 text-2xs text-faint">{t('ui.esc.search.count', { count, total })}</span> : null}
      {(query || scope !== 'all') && <Button size="xs" variant="ghost" onClick={() => { onQuery(''); onScope?.('all') }}>{t('ui.esc.search.clear')}</Button>}
    </div>
  </div>
}
