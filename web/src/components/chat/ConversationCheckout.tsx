import {useCallback, useEffect, useRef, useState} from 'react'
import {Folder, GitBranch} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import {runtimeRequest} from '@/lib/http'
import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import type {ResourceText} from '@/types'
import type {WorkspaceOptions} from '@/lib/protocol.generated'
import type {ConversationCheckout as Checkout} from '@/lib/conversationProject'
import {Button, RefreshButton, Switch} from '@/components/ui/primitives'
import {usePageVisible} from '@/hooks/usePageVisible'

type Status = 'loading' | 'ready' | 'error'

/** 新对话与项目目录选择器共用检出选项；读取只归属于当前目录和挂载代次。 */
export function ConversationCheckout({cwd, value, disabled = false, onChange}: {
  cwd: string; value: Checkout | null; disabled?: boolean
  onChange: (value: Checkout | null, status: Status) => void
}) {
  useLocale()
  const visible = usePageVisible()
  const [data, setData] = useState<{cwd: string; options: WorkspaceOptions} | null>(null)
  const [error, setError] = useState<ResourceText>('')
  const [revision, refresh] = useState(0)
  const [loading, setLoading] = useState(true)
  const latest = useRef({value, onChange})
  latest.current = {value, onChange}
  useEffect(() => {
    const controller = new AbortController()
    if (!visible || disabled) return
    setLoading(true); setError('')
    const previous = latest.current.value?.cwd === cwd ? latest.current.value : null
    latest.current.onChange(previous, 'loading')
    void runtimeRequest<WorkspaceOptions>('/workspace-options?' + new URLSearchParams({path: cwd}), 'GET', undefined, {signal: controller.signal}).then(options => {
      if (controller.signal.aborted) return
      setData({cwd, options}); setLoading(false)
      const mode = previous?.cwd === cwd && options.head ? previous.mode : 'local'
      const refs = mode === 'worktree' ? options.refs : options.local_branches
      const ref = previous?.cwd === cwd && previous.ref && refs.includes(previous.ref) ? previous.ref : options.branch ?? 'HEAD'
      latest.current.onChange({cwd, mode, ...(options.is_git ? {ref} : {}), expected_head: options.head, expected_branch: options.branch}, 'ready')
    }).catch(reason => {
      if (controller.signal.aborted) return
      setLoading(false); setError(failureText(reason)); latest.current.onChange(null, 'error')
    })
    return () => controller.abort()
  }, [cwd, visible, revision, disabled])
  const options = data?.cwd === cwd ? data.options : null
  if (error) return <div role="alert" className="flex flex-wrap items-center gap-2 text-xs text-danger">
    <span>{noticeText(error)}</span><Button size="xs" disabled={disabled} onClick={() => refresh(count => count + 1)}>{t('ui.requestComposition.retry')}</Button>
  </div>
  if (loading || !options || value?.cwd !== cwd) return <p role="status" className="text-xs text-muted">{t('ui.newConversation.checking')}</p>
  return <ConversationCheckoutFields options={options} value={value} disabled={disabled} onChange={onChange} onRefresh={() => refresh(count => count + 1)} />
}

/** 目录探测结果的共享呈现；选择仅更新草稿，不执行 Git 写入。 */
export function ConversationCheckoutFields({options, value, disabled = false, onChange, onRefresh}: {
  options: WorkspaceOptions; value: Checkout; disabled?: boolean
  onChange: (value: Checkout, status: Status) => void; onRefresh: () => void
}) {
  useLocale()
  const update = useCallback((next: Partial<Checkout>) => onChange({...value, ...next}, 'ready'), [value, onChange])
  if (!options.is_git) return <div className="refresh-action-container flex flex-wrap items-center gap-2 text-xs text-muted"><Folder className="h-3.5 w-3.5" /><span>{t(options.is_git === null ? 'ui.newConversation.gitDisabled' : 'ui.newConversation.folderOnly')}</span><RefreshButton size="xs" label={t('ui.newConversation.refresh')} disabled={disabled} onClick={onRefresh} /></div>
  const isolated = value.mode === 'worktree'
  const refs = isolated ? options.refs : options.local_branches
  const selected = value.ref ?? options.branch ?? 'HEAD'
  return <div className="space-y-2" data-conversation-checkout="true">
    <div className="refresh-action-container flex flex-wrap items-center gap-3 text-xs">
      <label className="flex min-w-0 items-center gap-1.5"><GitBranch className="h-3.5 w-3.5 shrink-0 text-muted" />
        <span className="sr-only">{t(isolated ? 'ui.newConversation.baseBranch' : 'ui.newConversation.branch')}</span>
        <select aria-label={t(isolated ? 'ui.newConversation.baseBranch' : 'ui.newConversation.branch')} value={selected} disabled={disabled}
          onChange={event => update({ref: event.target.value})} className="min-w-0 max-w-path rounded-lg border border-line bg-surface px-2 py-1.5">
          {[...new Set([selected, ...refs])].map(ref => <option key={ref} value={ref}>{ref}</option>)}
        </select>
      </label>
      <label className="flex items-center gap-2"><Switch checked={isolated} disabled={disabled || !options.head}
        onChange={event => update({mode: event.target.checked ? 'worktree' : 'local', ref: event.target.checked || options.local_branches.includes(selected) ? selected : options.branch ?? 'HEAD'})} />{t('ui.newConversation.worktree')}</label>
      <RefreshButton size="xs" label={t('ui.newConversation.refresh')} disabled={disabled} onClick={onRefresh} />
    </div>
    <p className="text-2xs text-muted">{t(isolated ? 'ui.newConversation.isolated' : 'ui.newConversation.local')}</p>
    {!options.head && <p className="text-2xs text-warn">{t('ui.newConversation.commitRequired')}</p>}
    {options.dirty && <p className="text-2xs text-warn">{t(isolated ? 'ui.newConversation.dirtyIsolated' : 'ui.newConversation.dirtyLocal')}</p>}
  </div>
}
