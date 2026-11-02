import {Folder, ChevronDown} from 'lucide-react'
import {useCallback} from 'react'
import {t, useLocale} from '@/i18n'
import {useAppStore} from '@/store/app'
import {Button} from '@/components/ui/primitives'
import {ConversationCheckout} from './ConversationCheckout'

export function NewConversationProject() {
  useLocale()
  const workspace = useAppStore(state => state.workspaces.find(row => row.id === state.activeWorkspaceId))
  const checkout = useAppStore(state => state.newSessionCheckout)
  const setCheckout = useAppStore(state => state.setNewSessionCheckout)
  const busy = useAppStore(state => state.sessionCreating)
  const revision = useAppStore(state => state.newSessionRevision)
  const applyCheckout = useCallback<typeof setCheckout>((value, status) => {
    const state = useAppStore.getState()
    if (state.activeSessionId === null && state.newSessionRevision === revision && state.activeWorkspaceId === workspace?.id) setCheckout(value, status)
  }, [revision, workspace?.id, setCheckout])
  return <div className="refresh-action-container flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-b-2xl bg-raised/60 px-2 py-0.5" aria-label={t('ui.newConversation.projectSettings')}>
    <Button size="xs" className="min-w-0 text-xs" variant="ghost" disabled={busy} title={workspace?.path} icon={<Folder className="h-3.5 w-3.5 shrink-0" />}
      iconRight={<ChevronDown className="h-3 w-3" />} onClick={() => useAppStore.getState().setWorkspacePickerOpen(true, 'select')}>
      <span className="max-w-path truncate">{workspace?.name ?? t('ui.newConversation.chooseProject')}</span>
    </Button>
    {workspace && <ConversationCheckout key={revision} compact cwd={workspace.path} value={checkout} disabled={busy} onChange={applyCheckout} />}
  </div>
}
