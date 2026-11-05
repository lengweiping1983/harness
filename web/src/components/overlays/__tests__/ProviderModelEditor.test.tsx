import {afterEach, expect, it, vi} from 'vitest'
import {isValidElement, type ReactElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {ProviderModelFields} from '../ProviderModelEditor'
import {setLocale, t} from '@/i18n'
import {providerPayload, type ProviderModel} from '@/lib/providerConnections'

vi.mock('@/i18n', async original => {
  const actual = await original<typeof import('@/i18n')>()
  return {...actual, useLocale: actual.getLocale}
})
afterEach(() => setLocale('zh-CN'))

function nodes(node: unknown): ReactElement<any>[] {
  if (Array.isArray(node)) return node.flatMap(nodes)
  if (!isValidElement<any>(node)) return []
  return [node, ...nodes(node.props.children)]
}

it('edits each model reasoning format and inherits without discarding other compatibility settings through English Chinese English', async () => {
  let draft: ProviderModel = {id: 'egg', model: 'egg-alpha', name: 'Egg 原文', enabled: true, input: ['text'],
    reasoning_efforts: {high: 'high', max: 'max'}, compat: {supportsStore: false, thinkingFormat: 'deepseek', vendorOption: 'Original 原文'}}
  const original = structuredClone(draft)
  const onChange = vi.fn((value: ProviderModel) => {draft = value})
  const tree = () => ProviderModelFields({value: draft, disabled: false, onChange})
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    const select = nodes(tree()).find(node => node.type === 'select' && node.props['aria-label'] === t('ui.providers.reasoningFormat'))!
    expect(select.props.value).toBe('deepseek')
    expect(renderToStaticMarkup(tree())).toContain(t('ui.providers.reasoningFormatOther', {format: 'deepseek'}))
    for (const format of ['openai', 'openrouter', '']) {
      const current = nodes(tree()).find(node => node.type === 'select' && node.props['aria-label'] === t('ui.providers.reasoningFormat'))!
      current.props.onChange({target: {value: format}})
      expect(draft.compat).toEqual({supportsStore: false, vendorOption: 'Original 原文', ...(format ? {thinkingFormat: format} : {})})
      expect(draft.reasoning_efforts).toEqual(original.reasoning_efforts)
      expect(draft.model).toBe('egg-alpha')
      expect(renderToStaticMarkup(tree())).toContain(renderToStaticMarkup(<>{t('ui.providers.reasoningFormatHint')}</>))
    }
    draft = structuredClone(original)
  }
  expect(onChange).toHaveBeenCalledTimes(9)
  const disabled = nodes(ProviderModelFields({value: draft, disabled: true, onChange})).find(node => node.type === 'select')!
  expect(disabled.props.disabled).toBe(true)
  const payload = providerPayload([{id: 'provider', type: 'custom', name: 'Provider', connections: [{id: 'endpoint', name: 'Endpoint',
    protocol: 'openai-completions', base_url: 'https://example.invalid/v1', enabled: true, auth: {type: 'environment', env: {}}, keys: [], models: [draft]}]}])
  expect(payload[0].connections[0].models[0]).toEqual(original)
})
