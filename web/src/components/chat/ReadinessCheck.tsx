import {useState} from 'react'
import {X} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import {useActiveSession, useAppStore} from '@/store/app'
import {newSessionDirectory, sessionDirectory} from '@/lib/workspaces'
import {Button} from '@/components/ui/primitives'

/** 只展示现有配置，不发网络探测、不注册目录或启动任何执行。 */
export function ReadinessCheck() {
  useLocale()
  const session = useActiveSession()
  const workspaces = useAppStore(s => s.workspaces)
  const activeWorkspaceId = useAppStore(s => s.activeWorkspaceId)
  const status = useAppStore(s => s.status)
  const directory = sessionDirectory(session) || newSessionDirectory(workspaces, activeWorkspaceId) || ''
  const [dismissed, setDismissed] = useState<string | null>(null)
  if (dismissed === directory) return null
  return <section className="mx-4 mt-3 rounded-xl border border-line bg-raised/50 p-3 text-xs text-muted" aria-label={t('ui.readiness.title')}>
    <div className="flex items-center justify-between gap-3"><h2 className="ui-card-title text-ink">{t('ui.readiness.title')}</h2><button type="button" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg hover:bg-raised" aria-label={t('ui.readiness.dismiss')} onClick={() => setDismissed(directory)}><X className="h-4 w-4" /></button></div>
    <p className="mt-1 break-all">{directory ? t('ui.readiness.project', {directory}) : t('ui.readiness.noProject')}</p>
    <p className="mt-1">{t(status?.has_model_credentials === true ? 'ui.readiness.model' : status?.has_model_credentials === false ? 'ui.readiness.noModel' : 'ui.readiness.modelUnknown')}</p>
    <p className="mt-1">{t('ui.readiness.hint')}</p>
    <div className="mt-2 flex flex-wrap gap-2"><Button size="sm" onClick={() => useAppStore.getState().setWorkspacePickerOpen(true, 'select')}>{t('ui.readiness.chooseProject')}</Button><Button size="sm" onClick={() => useAppStore.getState().setSettingsOpen(true, 'models')}>{t('ui.readiness.openModels')}</Button></div>
  </section>
}
