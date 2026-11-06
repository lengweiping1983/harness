import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {groupModelsByProvider} from '../modelMenu'
import {sessionModelOf} from '../workspaces'
import {ModelMenu, ModelSeat} from '@/components/chat/ModelPicker'
import {setLocale, t} from '@/i18n'
import {useAppStore} from '@/store/app'
import type {SessionSummary} from '@/types'

vi.mock('@/lib/connectionRoutes', () => ({useConnectionRoutes: () => null}))
vi.mock('@/store/app', async original => {
  const actual = await original<typeof import('@/store/app')>()
  return {...actual, useAppStore: Object.assign((selector: (state: ReturnType<typeof actual.useAppStore.getState>) => unknown) => selector(actual.useAppStore.getState()), actual.useAppStore),
    useActiveSession: () => {const state = actual.useAppStore.getState(); return state.sessions.find(session => session.id === state.activeSessionId) ?? null}}
})
afterEach(() => setLocale('zh-CN'))
beforeEach(() => useAppStore.setState({activeSessionId: null, sessions: [], models: [], defaultModel: '', defaultReasoningEffort: null,
  modelSaves: {}, defaultModelSave: null, reasoningSaves: {}, defaultReasoningSave: null}))
const models = [
  {id: 'first', label: 'First 原文', provider: ''},
  {id: 'second', label: 'Second 原文', provider: 'other'},
  {id: 'third', label: 'Third 原文', provider: 'Provider 原名'},
  {id: 'fourth', label: 'Fourth 原文', provider: ''},
]

it('keeps unspecified providers separate from a real provider named other without reordering models', () => {
  const groups = groupModelsByProvider(models)
  expect(groups.map(group => group.provider)).toEqual([null, 'other', 'Provider 原名'])
  expect(groups[0].models).toEqual([models[0], models[3]])
  expect(new Set(groups.map(group => JSON.stringify(group.provider))).size).toBe(3)
})

it('renders a localized unspecified heading and preserves real provider names', () => {
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    const html = renderToStaticMarkup(<ModelMenu models={models} current="first" onClose={() => undefined} />)
    expect(html).toContain(t('ui.models.unspecifiedProvider'))
    expect(html).toContain('>other</p>')
    expect(html).toContain('Provider 原名')
    expect(html).toContain('First 原文')
  }
})

it('uses the default until a model is chosen and retains manual choices across provider families', () => {
  expect(sessionModelOf(null, 'default-model')).toBe('default-model')
  expect(sessionModelOf({model: null}, 'default-model')).toBe('default-model')
  expect(sessionModelOf({model: ''}, 'default-model')).toBe('default-model')
  expect(sessionModelOf({model: 'chatgpt/manual'}, 'default-model')).toBe('chatgpt/manual')
  expect(sessionModelOf({model: 'harness/supplier/endpoint/model'}, 'chatgpt/default')).toBe('harness/supplier/endpoint/model')
})

it('shows the selected reasoning level beside the compact model name through English Chinese English', async () => {
  const id = 'harness/supplier/endpoint/crouton'
  useAppStore.setState({models: [{id, label: 'stealth/crouton-alpha', provider: 'supplier', supports_reasoning: true, reasoning_efforts: ['high', 'max']}],
    defaultModel: id, defaultReasoningEffort: 'max'})
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    const html = renderToStaticMarkup(<ModelSeat />)
    expect(html).toContain('crouton-a…')
    expect(html).toContain(`>${t('ui.models.reasoningSelection', {effort: 'max'})}</span>`)
    expect(html).toContain('aria-label="' + t('ui.models.selectionWithReasoning', {
      model: t('ui.components.chat.ModelPicker.8b625429e269', {p0: 'stealth/crouton-alpha'}),
      reasoning: t('ui.models.reasoningSelection', {effort: 'max'}),
    }) + '"')
    expect(html).not.toContain('harness/')
  }
})

it('follows conversation reasoning overrides and explicit model default without inheriting another conversation', () => {
  const id = 'model'
  const sessions = [
    {id: 'override', reasoning_effort: 'high', metadata: {reasoning_effort: 'high'}},
    {id: 'default', reasoning_effort: null, metadata: {reasoning_effort: ''}},
    {id: 'inherited', metadata: {}},
  ] as SessionSummary[]
  useAppStore.setState({models: [{id, label: 'Egg', provider: 'test', supports_reasoning: true, reasoning_efforts: ['high', 'max']}], defaultModel: id,
    defaultReasoningEffort: 'max', sessions})
  for (const [activeSessionId, effort] of [['override', 'high'], ['default', t('ui.components.chat.ModelPicker.9a36b187cea4')], ['inherited', 'max']]) {
    useAppStore.setState({activeSessionId})
    expect(renderToStaticMarkup(<ModelSeat />)).toContain(`>${t('ui.models.reasoningSelection', {effort})}</span>`)
  }
  useAppStore.setState({activeSessionId: null, defaultReasoningEffort: null})
  expect(renderToStaticMarkup(<ModelSeat />)).toContain(t('ui.models.reasoningSelection', {effort: t('ui.components.chat.ModelPicker.9a36b187cea4')}))
})

it('marks pending and unknown reasoning saves and unsupported choices without changing selection', () => {
  const id = 'model'
  useAppStore.setState({models: [{id, label: 'Egg', provider: 'test', supports_reasoning: true, reasoning_efforts: ['high']}], defaultModel: id,
    defaultReasoningEffort: 'max'})
  expect(renderToStaticMarkup(<ModelSeat />)).toContain(t('ui.models.reasoningUnsupported', {effort: 'max'}))
  for (const save of [{pending: true, error: null, unconfirmed: false}, {pending: false, error: 'Original failure', unconfirmed: true}]) {
    useAppStore.setState({defaultReasoningSave: save})
    expect(renderToStaticMarkup(<ModelSeat />)).toContain(t('ui.models.reasoningUnconfirmed', {effort: 'max'}))
  }
  useAppStore.setState({activeSessionId: 'override', sessions: [{id: 'override', reasoning_effort: 'high', metadata: {reasoning_effort: 'high'}}] as SessionSummary[]})
  expect(renderToStaticMarkup(<ModelSeat />)).toContain(t('ui.models.reasoningSelection', {effort: 'high'}))
  useAppStore.setState({activeSessionId: null, defaultReasoningEffort: null, defaultReasoningSave: null, models: [{id, label: 'Plain', provider: 'test', supports_reasoning: false, reasoning_efforts: []}]})
  expect(renderToStaticMarkup(<ModelSeat />)).not.toContain(t('ui.components.chat.ModelPicker.8cf0105d35ee'))
})

it('allows selecting an explicitly reported single reasoning level', () => {
  const single = [{id: 'single', label: 'Egg', provider: 'test', supports_reasoning: true, reasoning_efforts: ['max']}]
  const html = renderToStaticMarkup(<ModelMenu models={single} current="single" effort="max" onClose={() => undefined} />)
  expect(html).toContain('role="group" aria-label="' + t('ui.components.chat.ModelPicker.8cf0105d35ee') + '"')
  expect(html).toContain('>max</button>')
  expect(html).toContain('>' + t('ui.components.chat.ModelPicker.9a36b187cea4') + '</button>')
  expect(html).toContain('aria-pressed="true"')
})

it('selects inheritance independently of explicit levels and avoids repeating the selected model heading', () => {
  const reasoningModels = [{id: 'model', label: 'Unique 模型', provider: 'test', supports_reasoning: true, reasoning_efforts: ['high', 'max']}]
  for (const language of ['en', 'zh-CN', 'en'] as const) {
    setLocale(language)
    for (const kind of ['inherited', 'explicit', 'model-default', 'global'] as const) {
      useAppStore.setState({activeSessionId: kind === 'global' ? null : 'conversation', defaultReasoningEffort: 'high',
        sessions: [{id: 'conversation', ...(kind === 'inherited' ? {} : {reasoning_effort: kind === 'model-default' ? null : 'high', metadata: {reasoning_effort: kind === 'model-default' ? '' : 'high'}})}]})
      const html = renderToStaticMarkup(<ModelMenu models={reasoningModels} current="model" effort={kind === 'model-default' ? null : 'high'} onClose={() => undefined} />)
      expect(html.match(/Unique 模型/g)).toHaveLength(1)
      const selected = html.match(/<button[^>]*aria-pressed="true"[^>]*>(.*?)<\/button>/g) ?? []
      expect(selected).toHaveLength(1)
      expect(selected[0]).toContain('>' + (kind === 'inherited' ? t('ui.components.chat.ModelPicker.2fb47e73f109') : kind === 'model-default' ? t('ui.components.chat.ModelPicker.9a36b187cea4') : 'high') + '</button>')
    }
  }
})
