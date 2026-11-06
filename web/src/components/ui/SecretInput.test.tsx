import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {SecretInput} from './SecretInput'
import {setLocale, t} from '@/i18n'

const hooks = vi.hoisted(() => ({active: false, shown: false}))
vi.mock('react', async original => {
  const actual = await original<typeof import('react')>()
  return {...actual, useState: (initial: unknown) => hooks.active ? [hooks.shown, (value: boolean) => {hooks.shown = value}] : actual.useState(initial)}
})
vi.mock('@/i18n', async original => {const actual = await original<typeof import('@/i18n')>(); return {...actual, useLocale: actual.getLocale}})
function render(props: Parameters<typeof SecretInput>[0] = {}) {
  hooks.active = true
  try {return SecretInput(props)} finally {hooks.active = false}
}
beforeEach(() => {hooks.shown = false})
afterEach(async () => {await setLocale('en')})

it('places an icon without visible text inside the input boundary with space and an accessible name', async () => {
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    const html = renderToStaticMarkup(<SecretInput id="credential" aria-label="Credential" value="fixture" readOnly />)
    expect(html).toContain('relative block min-w-0')
    expect(html).toContain('pr-11')
    expect(html).toContain('absolute inset-y-1 right-1')
    expect(html).toContain(`aria-label="${t('ui.secrets.show')}"`)
    expect(html).toContain('aria-controls="credential"')
    expect(html).toContain('type="password"')
    expect(html.match(/<button\b[\s\S]*?<\/button>/)?.[0].replace(/<[^>]*>/g, '')).toBe('')
  }
})

it('toggles the original editable value and preserves focus without submitting or changing it', () => {
  const onChange = vi.fn(), preventDefault = vi.fn(), stopPropagation = vi.fn()
  const props = {value: 'Original secret value', onChange}
  const button = () => render(props).props.children[1]
  expect(render(props).props.children[0].props.type).toBe('password')
  button().props.onMouseDown({preventDefault})
  button().props.onClick({stopPropagation})
  expect(render(props).props.children[0].props.type).toBe('text')
  expect(render(props).props.children[0].props.value).toBe(props.value)
  expect(button().props['aria-pressed']).toBe(true)
  expect(button().props.children.props['aria-hidden']).toBe(true)
  button().props.onClick({stopPropagation})
  expect(render(props).props.children[0].props.type).toBe('password')
  expect(onChange).not.toHaveBeenCalled()
  expect(preventDefault).toHaveBeenCalledOnce()
})

it('supports controlled masked credentials, loading and disabled states without revealing on render', () => {
  const onToggleReveal = vi.fn()
  let tree = render({value: '••••mask', readOnly: true, concealedType: 'text', revealed: false, onToggleReveal})
  expect(tree.props.children[0].props.value).toBe('••••mask')
  expect(onToggleReveal).not.toHaveBeenCalled()
  tree.props.children[1].props.onClick({stopPropagation() {}})
  expect(onToggleReveal).toHaveBeenCalledOnce()
  for (const state of [{disabled: true}, {toggleDisabled: true}, {toggleBusy: true}]) {
    tree = render(state)
    expect(tree.props.children[1].props.disabled).toBe(true)
  }
})

it('conceals the editable value only when focus leaves the complete control', () => {
  hooks.shown = true
  render().props.onBlur({currentTarget: {contains: () => true}, relatedTarget: {}})
  expect(render().props.children[0].props.type).toBe('text')
  render().props.onBlur({currentTarget: {contains: () => false}, relatedTarget: null})
  expect(render().props.children[0].props.type).toBe('password')
})
