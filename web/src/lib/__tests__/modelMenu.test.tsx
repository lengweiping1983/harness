import {afterEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {groupModelsByProvider} from '../modelMenu'
import {sessionModelOf} from '../workspaces'
import {ModelMenu} from '@/components/chat/ModelPicker'
import {setLocale, t} from '@/i18n'

vi.mock('@/lib/connectionRoutes', () => ({useConnectionRoutes: () => null}))
afterEach(() => setLocale('zh-CN'))
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
