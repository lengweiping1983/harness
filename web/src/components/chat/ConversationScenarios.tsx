import {CalendarClock, CodeXml, Palette} from 'lucide-react'
import type {LucideIcon} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import {useAppStore} from '@/store/app'
import {useDraftStore} from '@/store/drafts'

interface ScenarioEntry {
  icon: LucideIcon
  sceneKey: string
  titleKey: string
  descriptionKey: string
  promptKey: string
}

const SCENARIO_ENTRIES: ScenarioEntry[] = [
  {
    icon: CalendarClock,
    sceneKey: 'ui.components.chat.Conversation.sceneOffice',
    titleKey: 'ui.components.chat.Conversation.5f3a9c1e8d2b',
    descriptionKey: 'ui.components.chat.Conversation.3d8a2c6e4b97',
    promptKey: 'ui.components.chat.Conversation.8a4c6e2b9d15',
  },
  {
    icon: CodeXml,
    sceneKey: 'ui.components.chat.Conversation.sceneDevelopment',
    titleKey: 'ui.components.chat.Conversation.7c4d2e9a6f18',
    descriptionKey: 'ui.components.chat.Conversation.6b1e4d8a2c59',
    promptKey: 'ui.components.chat.Conversation.4d9b3f7a1c68',
  },
  {
    icon: Palette,
    sceneKey: 'ui.components.chat.Conversation.sceneDesign',
    titleKey: 'ui.components.chat.Conversation.9e5f1b7c3a46',
    descriptionKey: 'ui.components.chat.Conversation.2c7f5a9d1e83',
    promptKey: 'ui.components.chat.Conversation.1e6a8c4d2b97',
  },
]

/**
 * 场景入口的落笔规则：与推荐任务一致，只填入输入框草稿，由用户编辑后发送；
 * 已有输入、附件或正在创建会话时不得覆盖用户状态。返回是否实际写入。
 */
export function chooseScenarioPrompt(promptKey: string): boolean {
  const state = useAppStore.getState()
  const draftKey = state.activeSessionId ?? '__unsaved__'
  const draft = useDraftStore.getState().bySession[draftKey]
  if (state.sessionCreating || draft?.text || draft?.attachments.length) return false
  useDraftStore.getState().setText(draftKey, t(promptKey))
  return true
}

/**
 * 开始页的场景入口：办公（定时任务）、开发（日常开发）、设计（网站设计）。
 */
export function ConversationScenarios({disabled = false}: {disabled?: boolean}) {
  useLocale()
  const choose = chooseScenarioPrompt
  return (
    <div className="welcome-scenarios grid w-full min-w-0 grid-cols-1 gap-2 sm:grid-cols-3" aria-label={t('ui.components.chat.Conversation.a7f2e5c9d481')}>
      {SCENARIO_ENTRIES.map(entry => {
        const Icon = entry.icon
        return (
          <button key={entry.titleKey} type="button" disabled={disabled} onClick={() => choose(entry.promptKey)}
            className="flex min-w-0 items-start gap-2.5 rounded-2xl border border-line bg-surface px-3.5 py-3 text-left transition-colors hover:border-brand/30 hover:bg-brand/5 focus-visible:outline-brand disabled:cursor-default disabled:opacity-55">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-brand/10 text-brand"><Icon className="h-4 w-4" aria-hidden="true" /></span>
            <span className="flex min-w-0 flex-col gap-0.5">
              <span className="text-2xs font-medium text-brand">{t(entry.sceneKey)}</span>
              <span className="text-compact font-medium text-ink">{t(entry.titleKey)}</span>
              <span className="break-words text-xs leading-5 text-muted">{t(entry.descriptionKey)}</span>
            </span>
          </button>
        )
      })}
    </div>
  )
}
