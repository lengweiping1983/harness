import {beforeEach, afterEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {Composer, type ComposerProps} from '@/components/chat/Composer'
import {useAppStore} from '@/store/app'
import {useDraftStore} from '@/store/drafts'
import {setLocale, t} from '@/i18n'

const fixture = vi.hoisted(() => ({
  voiceSupported: true,
  voiceTranscript: undefined as undefined | ((text: string) => void),
  choose: undefined as undefined | ((text: string) => void),
  pending: {current: null as null | {text: string; caret: number; draftKey: string}},
}))
vi.mock('@/hooks/useVoiceInputSupported', () => ({useVoiceInputSupported: () => fixture.voiceSupported}))

// 静态呈现不执行布局副作用；焦点目标由输入编辑器的独立生命周期实现。
vi.mock('react', async original => {
  const module = await original<typeof import('react')>()
  return {...module, useLayoutEffect: module.useEffect}
})
// 服务端组件夹具使用当前存储；保留实际输入区、推荐列表及草稿写入链。
vi.mock('@/store/app', async original => {
  const module = await original<typeof import('@/store/app')>()
  return {...module, useAppStore: Object.assign((select: (state: ReturnType<typeof module.useAppStore.getState>) => unknown) => select(module.useAppStore.getState()), module.useAppStore)}
})
vi.mock('@/store/drafts', async original => {
  const module = await original<typeof import('@/store/drafts')>()
  return {...module, useDraftStore: Object.assign((select: (state: ReturnType<typeof module.useDraftStore.getState>) => unknown) => select(module.useDraftStore.getState()), module.useDraftStore)}
})
vi.mock('@/hooks/useComposerEditor', () => ({useComposerEditor: (value: string) => ({
  taRef: {current: null}, dockRef: {current: null}, wrapRef: {current: null},
  pendingCompletion: fixture.pending, valueRef: {current: value}, resize: vi.fn(), scheduleFrame: vi.fn(), captureFrames: vi.fn(),
})}))
vi.mock('@/components/chat/VoiceButton', async original => {
  const module = await original<typeof import('@/components/chat/VoiceButton')>()
  return {VoiceButton: (props: Parameters<typeof module.VoiceButton>[0]) => {
    fixture.voiceTranscript = props.onTranscript
    return <module.VoiceButton {...props} />
  }}
})
vi.mock('@/components/chat/ConversationSuggestions', async original => {
  const module = await original<typeof import('@/components/chat/ConversationSuggestions')>()
  return {ConversationSuggestions: (props: Parameters<typeof module.ConversationSuggestions>[0]) => {
    fixture.choose = props.onChoose
    return <module.ConversationSuggestions {...props} />
  }}
})

const initialApp = useAppStore.getState(), initialDrafts = useDraftStore.getState()
const props: ComposerProps = {running: false, queued: 0, planMode: false, connection: 'open', showSuggestions: true,
  onSend: vi.fn(), onCancel: vi.fn(), onTogglePlan: vi.fn()}
const keys = ['b2b6ca2ea1f1', '8eb6b977850f', '0ac196393e3f', '20061c1959d0']

beforeEach(() => {
  vi.stubGlobal('window', {})
  fixture.choose = undefined; fixture.pending.current = null; fixture.voiceTranscript = undefined; fixture.voiceSupported = true
  useAppStore.setState({...initialApp, activeSessionId: null, activeWorkspaceId: null, workspaces: [], newSessionRevision: 1, sessionCreating: false})
  useDraftStore.setState({...initialDrafts, bySession: {}, submissions: {}})
})
afterEach(() => {setLocale('en'); useAppStore.setState(initialApp); useDraftStore.setState(initialDrafts); vi.restoreAllMocks(); vi.unstubAllGlobals()})

it('places the actual bilingual task list after the editor and project rail', () => {
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const html = renderToStaticMarkup(<Composer {...props} />)
    const list = html.indexOf('<ul class="welcome-suggestions')
    expect(list).toBeGreaterThan(html.indexOf('</textarea>'))
    expect(list).toBeGreaterThan(html.indexOf(t('ui.newConversation.chooseProject')))
    expect(html).toContain(`aria-label="${t('ui.newConversation.suggestions')}"`)
    const labels = keys.map(key => renderToStaticMarkup(<>{t('ui.components.chat.Conversation.' + key)}</>))
    for (const label of labels) expect(html.slice(list)).toContain(label)
    expect(labels.map(label => html.indexOf(label))).toEqual(labels.map(label => html.indexOf(label)).sort((a, b) => a - b))
  }
})

it.each([null, 'existing'])('fills only the %s conversation draft, requests the caret, and never sends or creates', sessionId => {
  useAppStore.setState({activeSessionId: sessionId})
  const create = vi.spyOn(useAppStore.getState(), 'materializeSession')
  renderToStaticMarkup(<Composer {...props} />)
  expect(fixture.choose).toBeTypeOf('function')
  fixture.choose!('Task 原文')
  const draftKey = sessionId ?? '__unsaved__'
  expect(useDraftStore.getState().bySession[draftKey].text).toBe('Task 原文')
  expect(fixture.pending.current).toEqual({text: 'Task 原文', caret: 7, draftKey})
  expect(props.onSend).not.toHaveBeenCalled()
  expect(create).not.toHaveBeenCalled()
  vi.restoreAllMocks()
})

it.each(['typing', 'attachment', 'new-draft', 'other-session', 'creating'] as const)('preserves user state when a stale suggestion meets %s', changed => {
  renderToStaticMarkup(<Composer {...props} />)
  if (changed === 'typing') useDraftStore.getState().setText('__unsaved__', 'Keep 用户')
  if (changed === 'attachment') useDraftStore.getState().updateAttachments('__unsaved__', () => [{localId: 'file', name: 'user.txt', size: 1, mediaType: 'text/plain', isImage: false, status: 'uploading'}])
  if (changed === 'new-draft') useAppStore.setState({newSessionRevision: 2})
  if (changed === 'other-session') useAppStore.setState({activeSessionId: 'other'})
  if (changed === 'creating') useAppStore.setState({sessionCreating: true})
  const before = useDraftStore.getState().bySession
  fixture.choose!('Old choice')
  expect(useDraftStore.getState().bySession).toBe(before)
  expect(fixture.pending.current).toBeNull()
})

it.each(['hidden', 'text', 'attachment', 'running', 'queued', 'submission'] as const)('keeps recommendations out of the %s editing state', state => {
  if (state === 'text') useDraftStore.getState().setText('__unsaved__', 'Keep draft')
  if (state === 'attachment') useDraftStore.getState().updateAttachments('__unsaved__', () => [{localId: 'file', name: 'user.txt', size: 1, mediaType: 'text/plain', isImage: false, status: 'uploading'}])
  if (state === 'submission') useDraftStore.getState().rememberSubmission('__unsaved__', {id: 'send', text: 'Pending', attachments: [], action: 'send', status: 'unknown'})
  const html = renderToStaticMarkup(<Composer {...props} showSuggestions={state !== 'hidden'} running={state === 'running'} queued={state === 'queued' ? 1 : 0} />)
  expect(html).not.toContain('welcome-suggestions')
  expect(fixture.choose).toBeUndefined()
})


it('places voice input immediately before send in the actual composer', async () => {
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    const html = renderToStaticMarkup(<Composer {...props} />)
    const microphone = html.indexOf(`aria-label="${t('ui.voice.start')}"`)
    const send = html.indexOf(`aria-label="${t('ui.components.chat.Composer.edecf0ae6e51')}"`)
    expect(microphone).toBeGreaterThan(0)
    expect(send).toBeGreaterThan(microphone)
    expect(html.slice(microphone, send)).not.toContain('data-lucide="scissors"')
  }
})

it('omits voice input and its recording component when the server CPU is unsupported', async () => {
  fixture.voiceSupported = false
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(language)
    const html = renderToStaticMarkup(<Composer {...props} />)
    expect(html).not.toContain(`aria-label="${t('ui.voice.start')}"`)
    expect(html).toContain(`aria-label="${t('ui.components.chat.Composer.edecf0ae6e51')}"`)
    expect(fixture.voiceTranscript).toBeUndefined()
  }
})


it('appends dictation to the latest actual composer draft without sending', () => {
  useDraftStore.getState().setText('__unsaved__', 'Original 用户')
  renderToStaticMarkup(<Composer {...props} />)
  useDraftStore.getState().setText('__unsaved__', 'Latest 用户')
  fixture.voiceTranscript!('Speech 原文')
  expect(useDraftStore.getState().bySession.__unsaved__.text).toBe('Latest 用户 Speech 原文')
  expect(props.onSend).not.toHaveBeenCalled()
})
