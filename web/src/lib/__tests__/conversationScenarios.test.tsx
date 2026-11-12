import {afterEach, beforeEach, expect, it} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {ConversationScenarios, chooseScenarioPrompt} from '@/components/chat/ConversationScenarios'
import {useAppStore} from '@/store/app'
import {useDraftStore} from '@/store/drafts'
import {setLocale, t} from '@/i18n'

const titles = ['5f3a9c1e8d2b', '7c4d2e9a6f18', '9e5f1b7c3a46']
const descriptions = ['3d8a2c6e4b97', '6b1e4d8a2c59', '2c7f5a9d1e83']
const scenes = ['sceneOffice', 'sceneDevelopment', 'sceneDesign']

const initialApp = useAppStore.getState(), initialDrafts = useDraftStore.getState()

beforeEach(() => {
  useAppStore.setState({...initialApp, activeSessionId: null, sessionCreating: false})
  useDraftStore.setState({...initialDrafts, bySession: {}, submissions: {}})
})
afterEach(() => {setLocale('en'); useAppStore.setState(initialApp); useDraftStore.setState(initialDrafts)})

it('renders the three scenario entries bilingually with localized copy', () => {
  for (const language of ['en', 'zh-CN'] as const) {
    setLocale(language)
    const html = renderToStaticMarkup(<ConversationScenarios />)
    expect(html).toContain(`aria-label="${t('ui.components.chat.Conversation.a7f2e5c9d481')}"`)
    for (const key of [...titles, ...descriptions, ...scenes]) expect(html).toContain(t('ui.components.chat.Conversation.' + key))
  }
})

it.each([null, 'existing'])('fills only the %s conversation draft without sending', sessionId => {
  useAppStore.setState({activeSessionId: sessionId})
  expect(chooseScenarioPrompt('ui.components.chat.Conversation.8a4c6e2b9d15')).toBe(true)
  const draftKey = sessionId ?? '__unsaved__'
  expect(useDraftStore.getState().bySession[draftKey].text).toBe(t('ui.components.chat.Conversation.8a4c6e2b9d15'))
})

it.each(['typing', 'attachment', 'creating'] as const)('never overwrites user state during %s', changed => {
  if (changed === 'typing') useDraftStore.getState().setText('__unsaved__', 'Keep 用户')
  if (changed === 'attachment') useDraftStore.getState().updateAttachments('__unsaved__', () => [{localId: 'file', name: 'user.txt', size: 1, mediaType: 'text/plain', isImage: false, status: 'uploading'}])
  if (changed === 'creating') useAppStore.setState({sessionCreating: true})
  const before = useDraftStore.getState().bySession
  expect(chooseScenarioPrompt('ui.components.chat.Conversation.4d9b3f7a1c68')).toBe(false)
  expect(useDraftStore.getState().bySession).toBe(before)
})

it('fills the composer with the skill command for development and design entries', () => {
  expect(chooseScenarioPrompt('ui.components.chat.Conversation.4d9b3f7a1c68')).toBe(true)
  expect(useDraftStore.getState().bySession.__unsaved__.text).toBe('/skill project-onboarding ')
  useDraftStore.getState().setText('__unsaved__', '')
  expect(chooseScenarioPrompt('ui.components.chat.Conversation.1e6a8c4d2b97')).toBe(true)
  expect(useDraftStore.getState().bySession.__unsaved__.text).toBe('/skill website-design ')
})
