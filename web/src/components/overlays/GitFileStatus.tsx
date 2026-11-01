import { t, useLocale } from '@/i18n'

export function gitFileStatusKeys(status: string): string[] {
  if (status === '??') return ['untracked']
  if (status === '!!') return ['ignored']
  if (['DD', 'AU', 'UD', 'UA', 'DU', 'AA', 'UU'].includes(status) || status.includes('U')) return ['conflict']
  const labels: Record<string, string> = { M: 'modified', A: 'added', D: 'deleted', R: 'renamed', C: 'copied', T: 'typeChanged' }
  const changes = [...new Set([...status].filter(code => code !== ' ').map(code => labels[code] ?? 'unknown'))]
  if (!changes.length) return ['unknown']
  if (status.length === 2 && status[0] !== ' ') changes.push(status[1] !== ' ' ? 'partial' : 'staged')
  return changes
}

export function GitFileStatus({ status }: { status: string }) {
  useLocale()
  const keys = gitFileStatusKeys(status)
  return <span title={status} className={`inline-flex shrink-0 flex-wrap gap-1 font-sans text-2xs ${keys.includes('conflict') ? 'text-danger' : 'text-brand'}`}>
    {keys.map(key => <span key={key} className="rounded border border-current/20 bg-raised px-1.5 py-0.5">{t('ui.git.status.' + key)}</span>)}
  </span>
}
