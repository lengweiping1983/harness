import {ArrowUpRight} from 'lucide-react'
import {t, useLocale} from '@/i18n'

const SUGGESTION_KEYS = [
  'ui.components.chat.Conversation.b2b6ca2ea1f1',
  'ui.components.chat.Conversation.8eb6b977850f',
  'ui.components.chat.Conversation.0ac196393e3f',
  'ui.components.chat.Conversation.20061c1959d0',
]

/** 推荐任务只填入输入框，由用户编辑后发送。 */
export function ConversationSuggestions({onChoose, disabled = false}: {onChoose: (text: string) => void; disabled?: boolean}) {
  useLocale()
  return <ul className="welcome-suggestions flex min-w-0 flex-col items-start gap-2 px-1 pb-2 pt-6" aria-label={t('ui.newConversation.suggestions')}>
    {SUGGESTION_KEYS.map(key => <li key={key} className="max-w-full">
      <button type="button" disabled={disabled} onClick={() => onChoose(t(key))}
        onKeyDown={event => {if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) event.preventDefault()}}
        className="flex min-h-9 max-w-full items-center gap-2 rounded-full border border-line bg-surface px-3.5 py-2 text-left text-compact text-ink transition-colors hover:border-brand/30 hover:bg-brand/5 focus-visible:outline-brand disabled:cursor-default disabled:opacity-55">
        <span className="min-w-0 break-words">{t(key)}</span>
        <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden="true" />
      </button>
    </li>)}
  </ul>
}
