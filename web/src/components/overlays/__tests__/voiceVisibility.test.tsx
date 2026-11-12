import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import type {ReactElement} from 'react'
import {SettingsDialog} from '../SettingsDialog'
import {CommandPalette} from '../CommandPalette'
import {useAppStore} from '@/store/app'
import {setLocale, t} from '@/i18n'

const hooks=vi.hoisted(() => ({cursor:0, slots:[] as unknown[], cleanups:new Map<number, () => void>(), supported:false}))
vi.mock('react',async original => {
  const module=await original<typeof import('react')>()
  const effect=(run:() => void | (() => void),deps:unknown[]) => {
    const index=hooks.cursor++, previous=hooks.slots[index] as unknown[] | undefined
    if (!previous || deps.some((value,i) => value !== previous[i])) {
      hooks.cleanups.get(index)?.(); hooks.slots[index]=deps
      const cleanup=run(); if (cleanup) hooks.cleanups.set(index,cleanup)
    }
  }
  return {...module,
    useState:(initial:unknown) => {
      const index=hooks.cursor++
      if (!(index in hooks.slots)) hooks.slots[index]=typeof initial==='function' ? initial() : initial
      return [hooks.slots[index],(value:unknown) => {hooks.slots[index]=typeof value==='function' ? value(hooks.slots[index]) : value}]
    },
    useRef:(value:unknown) => {const index=hooks.cursor++; return hooks.slots[index] ??= {current:value}},
    useMemo:(compute:() => unknown) => compute(), useId:() => 'voice-test',
    useEffect:effect,useLayoutEffect:effect,
    useSyncExternalStore:(_subscribe:unknown,snapshot:() => unknown) => snapshot(),
  }
})
vi.mock('@/i18n',async original => {const module=await original<typeof import('@/i18n')>(); return {...module,useLocale:module.getLocale}})
vi.mock('@/hooks/useVoiceInputSupported',() => ({useVoiceInputSupported:() => hooks.supported}))
vi.mock('@/store/app',async original => {
  const module=await original<typeof import('@/store/app')>()
  return {...module,useAppStore:Object.assign((select:(state:ReturnType<typeof module.useAppStore.getState>) => unknown) => select(module.useAppStore.getState()),module.useAppStore)}
})
const initialApp=useAppStore.getState()
const render=(component:() => unknown) => {hooks.cursor=0; return component()}
function nodes(node:unknown):ReactElement<Record<string,any>>[] {
  if (!node || typeof node!=='object') return []
  if (Array.isArray(node)) return node.flatMap(nodes)
  const element=node as ReactElement<Record<string,any>>
  return [element,...nodes(element.props?.children)]
}
const text=(node:unknown):string => typeof node==='string' ? node : Array.isArray(node) ? node.map(text).join('')
  : node && typeof node==='object' ? text((node as ReactElement<{children?:unknown}>).props?.children) : ''
beforeEach(() => {
  hooks.slots=[]; hooks.cleanups.clear(); hooks.supported=false
  useAppStore.setState({...initialApp,settingsOpen:true,settingsTarget:'voice',commandOpen:true,activeSessionId:null,workspaces:[],workspace:null})
  vi.stubGlobal('window',{addEventListener:vi.fn(),removeEventListener:vi.fn()})
})
afterEach(async () => {
  for (const cleanup of hooks.cleanups.values()) cleanup()
  useAppStore.setState(initialApp); vi.unstubAllGlobals(); await setLocale('en')
})

it('removes unsupported voice settings from navigation, search and directed page mounting in English, Chinese, English',async () => {
  for (const language of ['en','zh-CN','en'] as const) {
    await setLocale(language)
    render(SettingsDialog)
    let elements=nodes(render(SettingsDialog))
    expect(elements.some(node => node.type==='button' && node.props['data-page']==='voice')).toBe(false)
    expect(elements.find(node => node.props.id==='settings-content')?.props['data-page']).toBe('general')
    elements.find(node => node.type==='input' && node.props.type==='search')!.props.onChange({target:{value:t('ui.voice.dictation.title')}})
    elements=nodes(render(SettingsDialog))
    expect(elements.some(node => node.type==='button' && node.props['data-page']==='voice')).toBe(false)
    expect(text(render(SettingsDialog))).toContain(t('ui.settings.searchEmpty'))
  }
})

it('keeps supported voice settings selectable and mounts the targeted page',() => {
  hooks.supported=true
  render(SettingsDialog)
  const elements=nodes(render(SettingsDialog))
  expect(elements.some(node => node.type==='button' && node.props['data-page']==='voice')).toBe(true)
  expect(elements.find(node => node.props.id==='settings-content')?.props['data-page']).toBe('voice')
})

it.each([false,true])('uses CPU support %s in the actual command palette and voice search',async supported => {
  hooks.supported=supported
  for (const language of ['en','zh-CN','en'] as const) {
    await setLocale(language)
    const elements=nodes(render(CommandPalette))
    elements.find(node => node.props['data-command-search']!==undefined)!.props.onChange({target:{value:t('ui.voice.dictation.title')}})
    const choices=nodes(render(CommandPalette)).filter(node => node.props.role==='option')
    expect(choices.some(node => text(node).includes(t('ui.voice.dictation.title')))).toBe(supported)
  }
})
