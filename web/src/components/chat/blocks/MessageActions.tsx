import { t, useLocale } from '@/i18n'
import { memo } from 'react'
import { Copy, Pencil, Loader2, Target } from 'lucide-react'
import { cn, copyText } from '@/lib/utils'
import { useAppStore } from '@/store/app'

/** 消息底部操作条：目标标识常显，复制和编辑悬停或聚焦时显示。 */

export interface MessageActionsProps {
  /** 复制用的原始 Markdown 文本。 */
  text: string
  /** aria-label 里的来源描述，例如「助手回复」「你的提问」。 */
  label: string
  goalId?: string
  hideControls?: boolean
  onSetGoal?: () => void
  settingGoal?: boolean
  goalDisabled?: boolean
  onEdit?: () => void
  editing?: boolean
  className?: string
}

export const MessageActions = memo(function MessageActions({
  text,
  label,
  className,
  onEdit,
  onSetGoal,
  goalId,
  hideControls = false,
  settingGoal = false,
  goalDisabled = false,
  editing = false,
}: MessageActionsProps) {
  useLocale()

  const toast = useAppStore((s) => s.toast)

  const onCopy = async () => {
    const ok = await copyText(text)
    if (ok) toast({ title: t("ui.components.chat.blocks.MessageActions.cce28dd1fcdf"), description: label, tone: 'success' })
    else toast({ title: t("ui.components.chat.blocks.MessageActions.753d8bb0da99"), description: t("ui.components.chat.blocks.MessageActions.bc3be7231933"), tone: 'danger' })
  }

  return (
    <div data-message-actions=""
      className={cn(
        'flex items-center gap-0.5',
        className,
      )}
    >
      {!hideControls && <div className="flex items-center gap-0.5 opacity-0 transition-opacity duration-150 group-hover/message:opacity-100 focus-within:opacity-100 pointer-coarse:opacity-100">
      {onEdit ? <ActionButton
        icon={editing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Pencil className="h-3.5 w-3.5" />}
        label={t('ui.messageEdit.action')} hint={t('ui.messageEdit.hint')}
        onClick={onEdit} disabled={editing}
      /> : null}
      <ActionButton
        icon={<Copy className="h-3.5 w-3.5" />}
        label={t("ui.components.chat.blocks.MessageActions.2a76bfd72978", {p0: (label)})}
        hint={t("ui.components.chat.blocks.MessageActions.2e8a27d3a971")}
        onClick={() => void onCopy()}
      />
      </div>}
      {goalId && <span data-goal-id={goalId} className="ml-1 inline-flex h-6 items-center gap-1 rounded-full bg-brand/10 px-2 text-xs font-medium text-brand">
        <Target className="h-3.5 w-3.5" aria-hidden="true" />{t('ui.goal.messageBadge')}
      </span>}
      {onSetGoal && <button type="button" disabled={settingGoal || goalDisabled} onClick={onSetGoal}
        aria-label={t('ui.goal.setFromMessage')} title={t(goalDisabled ? 'ui.goal.alreadyExists' : 'ui.goal.setFromMessage')}
        className="ml-1 inline-flex h-6 items-center gap-1 rounded-md px-1 text-xs text-muted hover:bg-raised hover:text-ink focus-visible:outline-brand disabled:opacity-40">
        {settingGoal ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Target className="h-3.5 w-3.5" />}
        {t('ui.goal.setFromMessage')}
      </button>}
    </div>
  )
})

function ActionButton({
  icon,
  label,
  hint,
  onClick,
  disabled = false,
}: {
  icon: React.ReactNode
  label: string
  hint: string
  onClick: () => void
  disabled?: boolean
}) {
  useLocale()

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={hint}
      className={cn(
        'inline-flex h-6 w-6 items-center justify-center rounded-md text-faint',
        'transition-colors duration-150 hover:bg-raised hover:text-ink',
        'focus-visible:bg-raised focus-visible:text-ink focus-visible:outline-none',
      )}
    >
      {icon}
    </button>
  )
}
