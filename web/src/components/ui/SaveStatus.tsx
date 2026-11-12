import {t, useLocale} from '@/i18n'
import {cn} from '@/lib/utils'

export type SavePhase = 'unsaved' | 'saving' | 'saved' | 'failed' | 'unknown'

/** 共用状态词；调用方继续负责原有保存、回查和生效语义。 */
export function SaveStatus({phase}: {phase: SavePhase}) {
  useLocale()
  return <span role="status" data-save-phase={phase} className={cn('text-2xs', phase === 'failed' ? 'text-danger' : phase === 'unknown' ? 'text-warn' : 'text-muted')}>{t('ui.saveStatus.' + phase)}</span>
}
