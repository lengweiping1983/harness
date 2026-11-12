import { t, useLocale } from '@/i18n'
import { TEAM_TEMPLATES, teamTemplate, templateKey } from '@/lib/teamTemplates'
import type { AgentRow } from '@/lib/extensionCatalog'
import { Button } from '@/components/ui/primitives'

export function TeamTemplates({ onChoose, disabled = false, agents = [] }: { onChoose: (team: AgentRow) => void; disabled?: boolean; agents?: readonly Pick<AgentRow, 'name'>[] }) {
  useLocale()
  return <details className="rounded-xl border border-line bg-raised/40 p-3" open>
    <summary className="cursor-pointer text-sm font-medium text-ink">{t('ui.teamTemplates.title')}</summary>
    <p className="mt-2 text-xs leading-relaxed text-muted">{t('ui.teamTemplates.hint')}</p>
    <div className="mt-3 grid gap-3 sm:grid-cols-2">
      {TEAM_TEMPLATES.map(template => <article key={template.id} className="flex flex-col gap-2 rounded-lg border border-line bg-canvas p-3">
        <h4 className="ui-card-title">{t(templateKey(template.id, 'name'))}</h4>
        <p className="flex-1 text-xs leading-relaxed text-muted">{t(templateKey(template.id, 'description'))}</p>
        <p className="text-2xs text-faint">{t(template.writes ? 'ui.teamTemplates.serialWrite' : 'ui.teamTemplates.readOnly')}</p>
        <Button size="sm" variant="secondary" disabled={disabled} aria-label={t('ui.teamTemplates.chooseNamed', {name: t(templateKey(template.id, 'name'))})} onClick={() => onChoose(teamTemplate(template.id, agents))}>{t('ui.teamTemplates.choose')}</Button>
      </article>)}
    </div>
  </details>
}
