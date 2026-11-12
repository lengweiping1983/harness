import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import type {ReactElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {VoiceInputPage} from '../VoiceInputPage'
import {setLocale, t} from '@/i18n'
import {SETTINGS_PAGES} from '@/lib/settingsCatalog'
import {DEFAULT_VOICE_SHORTCUT, formatVoiceShortcut} from '@/lib/desktopVoice'

const hooks = vi.hoisted(() => ({cursor:0, slots:[] as unknown[], cleanups:new Map<number, () => void>()}))
vi.mock('react', async original => ({...await original<typeof import('react')>(),
  useRef:(value:unknown) => {const index=hooks.cursor++; return hooks.slots[index] ??= {current:value}},
  useState:(value:unknown) => {const index=hooks.cursor++; if (!(index in hooks.slots)) hooks.slots[index]=value
    return [hooks.slots[index], (next:unknown) => {hooks.slots[index]=next}]},
  useEffect:(effect:() => void | (() => void), deps:unknown[]) => {
    const index=hooks.cursor++, previous=hooks.slots[index] as unknown[] | undefined
    if (!previous || deps.some((dep,i) => dep !== previous[i])) {
      hooks.cleanups.get(index)?.(); hooks.slots[index]=deps
      const cleanup=effect(); if (cleanup) hooks.cleanups.set(index,cleanup)
    }
  },
  useSyncExternalStore:(_subscribe:unknown,snapshot:() => unknown) => snapshot(),
}))
vi.mock('@/i18n', async original => {const module=await original<typeof import('@/i18n')>(); return {...module,useLocale:module.getLocale}})
const capabilities={available:true,cpu_supported:true,platform:'macos',download_url:'https://anoma.ly/hex/download/macos-arm64-dmg',reason:null}
const status={supported:true,enabled:false,shortcut:'Control+Alt+Space',accessibility:false,phase:'idle',error:null}
const bridge={status:vi.fn(),configure:vi.fn(),permission:vi.fn()}
const fetchMock=vi.fn<typeof fetch>()
const render=() => {hooks.cursor=0; return VoiceInputPage()}
const markup=() => renderToStaticMarkup(render())
function nodes(node:unknown): ReactElement<Record<string,any>>[] {
  if (!node || typeof node !== 'object') return []
  if (Array.isArray(node)) return node.flatMap(nodes)
  const value=node as ReactElement<Record<string,any>>
  return [value,...nodes(value.props?.children)]
}
const save=() => nodes(render()).find(node => node.props.children===t('ui.voice.global.save'))!.props.onClick()
const checkbox=() => nodes(render()).find(node => node.type==='input' && node.props.type==='checkbox')!
const shortcut=() => nodes(render()).find(node => node.type==='input' && node.props.spellCheck===false)!
const refresh=() => nodes(render()).find(node => node.props.label===t('ui.voice.dictation.refresh'))!.props.onClick()
function unmount() {for (const cleanup of [...hooks.cleanups.values()].reverse()) cleanup(); hooks.cleanups.clear()}
function deferred<T>() {let resolve!:(value:T) => void; const promise=new Promise<T>(done => {resolve=done}); return {promise,resolve}}
beforeEach(() => {
  hooks.cursor=0; hooks.slots=[]; hooks.cleanups.clear(); fetchMock.mockReset()
  bridge.status.mockReset().mockResolvedValue({ok:true,data:status})
  bridge.configure.mockReset().mockResolvedValue({ok:true,data:{...status,enabled:true}})
  bridge.permission.mockReset().mockResolvedValue({ok:true,data:status})
  vi.stubGlobal('window',{harnessDesktop:{voice:bridge}})
  vi.stubGlobal('navigator',{platform:'MacIntel'})
  vi.stubGlobal('fetch',fetchMock)
  vi.stubGlobal('document',{hidden:false,documentElement:{},addEventListener:vi.fn(),removeEventListener:vi.fn()})
  fetchMock.mockImplementation(async () => Response.json(capabilities))
})
afterEach(async () => {unmount(); vi.unstubAllGlobals(); await setLocale('en')})

it('checks the engine and owned desktop settings without launching Hex or showing an installer through English, Chinese, English',async () => {
  expect(SETTINGS_PAGES.find(page => page.id==='voice')).toMatchObject({group:'basic',labelKey:'ui.voice.dictation.title'})
  render()
  await vi.waitFor(() => expect(markup()).not.toContain(t('ui.voice.dictation.checking')))
  for (const locale of ['en','zh-CN','en'] as const) {
    await setLocale(locale)
    const html=markup()
    expect(html).toContain(t('ui.voice.dictation.composer',{shortcut:formatVoiceShortcut('Control+Shift+Space')}))
    expect(html).not.toContain('Hex')
    expect(html).not.toContain('The multilingual model is prepared when needed')
    expect(html).not.toContain('多语言模型按需准备')
    expect(html).toContain(t('ui.voice.global.toggleHelp',{shortcut:formatVoiceShortcut(DEFAULT_VOICE_SHORTCUT)}))
    expect(html).not.toContain(capabilities.download_url)
    expect(nodes(render()).filter(node => node.type === 'a')).toHaveLength(0)
  }
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(bridge.configure).not.toHaveBeenCalled()
  expect(bridge.permission).not.toHaveBeenCalled()
})

it.each([
  ['MacIntel','⌃ + ⌥ + Space','⌘ + ⇧ + K','Command+Shift+K'],
  ['Win32','Ctrl + Alt + Space','Ctrl + Shift + K','Control+Shift+K'],
])('uses native shortcut names in the field and help on %s, then saves the accelerator once',async (platform,initial,edited,accelerator) => {
  vi.stubGlobal('navigator',{platform})
  render(); await vi.waitFor(() => expect(checkbox().props.disabled).toBe(false))
  for (const locale of ['en','zh-CN','en'] as const) {
    await setLocale(locale)
    expect(shortcut().props.value).toBe(initial)
    expect(markup()).toContain(t('ui.voice.global.toggleHelp',{shortcut:initial}))
    expect(markup()).not.toContain('Control+Alt+Space')
  }
  expect(bridge.configure).not.toHaveBeenCalled()
  expect(bridge.permission).not.toHaveBeenCalled()
  expect(shortcut().props.readOnly).toBe(true)
  const nativeEvent={code:'KeyK',key:'K',ctrlKey:platform==='Win32',metaKey:platform==='MacIntel',shiftKey:true,altKey:false}
  shortcut().props.onKeyDown({nativeEvent,...nativeEvent,preventDefault:vi.fn(),stopPropagation:vi.fn()})
  bridge.configure.mockResolvedValueOnce({ok:true,data:{...status,shortcut:accelerator}})
  await save()
  expect(bridge.configure).toHaveBeenCalledExactlyOnceWith({enabled:false,shortcut:accelerator})
  expect(shortcut().props.value).toBe(edited)
  expect(fetchMock).toHaveBeenCalledOnce()
})

it.each(['MacIntel','Win32'])('keeps unedited stored modifier aliases when saving on %s',async platform => {
  vi.stubGlobal('navigator',{platform})
  const accelerator='CommandOrControl+Option+K'
  bridge.status.mockResolvedValue({ok:true,data:{...status,shortcut:accelerator}})
  render(); await vi.waitFor(() => expect(checkbox().props.disabled).toBe(false))
  expect(shortcut().props.value).toBe(formatVoiceShortcut(accelerator))
  await save()
  expect(bridge.configure).toHaveBeenCalledExactlyOnceWith({enabled:false,shortcut:accelerator})
})

it('does not enable dictation until an explicit save and serializes double clicks',async () => {
  render(); await vi.waitFor(() => expect(bridge.status).toHaveBeenCalledOnce())
  await vi.waitFor(() => expect(checkbox().props.disabled).toBe(false))
  checkbox().props.onChange({target:{checked:true}})
  expect(bridge.configure).not.toHaveBeenCalled()
  const pending=deferred<any>()
  bridge.configure.mockReturnValueOnce(pending.promise)
  const first=save(); await save()
  expect(bridge.configure).toHaveBeenCalledExactlyOnceWith({enabled:true,shortcut:'Control+Alt+Space'})
  pending.resolve({ok:true,data:{...status,enabled:true}})
  await first
  expect(markup()).toContain(t('ui.voice.global.enabled'))
  expect(markup()).toContain(t('ui.voice.global.saved'))
})

it('keeps a missing engine distinct from desktop availability and never links to a separate Hex installation',async () => {
  fetchMock.mockResolvedValue(Response.json({...capabilities,available:false,reason:{key:'ui.voice.dictation.missing'}}))
  render()
  await vi.waitFor(() => expect(markup()).toContain(t('ui.voice.dictation.missing')))
  const control=nodes(render()).find(node => node.props.children===t('ui.voice.global.save'))!
  expect(control.props.disabled).toBe(true)
  expect(markup()).not.toContain(capabilities.download_url)
  await control.props.onClick()
  expect(bridge.configure).not.toHaveBeenCalled()
})

it('a browser offers composer input and explains the desktop requirement without opening another app',async () => {
  vi.stubGlobal('window',{})
  render()
  await vi.waitFor(() => expect(markup()).not.toContain(t('ui.voice.dictation.checking')))
  expect(markup()).toContain(t('ui.voice.global.desktopRequired'))
  expect(markup()).not.toContain(t('ui.voice.global.save'))
  expect(bridge.status).not.toHaveBeenCalled()
})

it('a lost save result is read back once without replaying its accepted write',async () => {
  render(); await vi.waitFor(() => expect(checkbox().props.disabled).toBe(false))
  bridge.configure.mockRejectedValueOnce(Error('transport lost'))
  await save()
  expect(bridge.configure).toHaveBeenCalledOnce()
  expect(bridge.status).toHaveBeenCalledTimes(2)
  for (const locale of ['en','zh-CN','en'] as const) {
    await setLocale(locale)
    expect(markup()).toContain(t('ui.voice.global.saveUnconfirmed'))
  }
})

it('does not apply a late read over edits, a closed page, or a hidden page',async () => {
  render(); await vi.waitFor(() => expect(checkbox().props.disabled).toBe(false))
  const response=deferred<any>()
  bridge.status.mockReturnValueOnce(response.promise)
  refresh()
  checkbox().props.onChange({target:{checked:true}})
  response.resolve({ok:true,data:status})
  await vi.waitFor(() => expect(markup()).not.toContain(t('ui.voice.dictation.checking')))
  expect(checkbox().props.checked).toBe(true)
  const late=deferred<any>()
  bridge.status.mockReturnValueOnce(late.promise)
  refresh(); unmount()
  late.resolve({ok:true,data:{...status,enabled:true}})
  await late.promise
  expect(bridge.configure).not.toHaveBeenCalled()
})

it('keeps damaged saved settings visible and does not auto repair them on refresh',async () => {
  bridge.status.mockResolvedValue({ok:true,data:{...status,error:{key:'ui.voice.global.settingsInvalid'}}})
  render()
  await vi.waitFor(() => expect(markup()).toContain(t('ui.voice.global.settingsInvalid')))
  refresh()
  await vi.waitFor(() => expect(bridge.status).toHaveBeenCalledTimes(2))
  expect(bridge.configure).not.toHaveBeenCalled()
})

it('keeps the saved shortcut through bare letters, composition and repeats, then captures one chord',async () => {
  render(); await vi.waitFor(() => expect(checkbox().props.disabled).toBe(false))
  const key={code:'KeyD',key:'d',ctrlKey:false,altKey:false,metaKey:false,shiftKey:false}
  for(const extra of [{},{isComposing:true,ctrlKey:true},{keyCode:229,ctrlKey:true},{repeat:true,ctrlKey:true},{code:'ControlLeft',key:'Control',ctrlKey:true}]) {
    const nativeEvent={...key,...extra}
    shortcut().props.onKeyDown({...nativeEvent,nativeEvent,preventDefault:vi.fn(),stopPropagation:vi.fn()})
    expect(shortcut().props.value).toBe(formatVoiceShortcut(DEFAULT_VOICE_SHORTCUT))
  }
  const nativeEvent={...key,ctrlKey:true,altKey:true}
  shortcut().props.onKeyDown({...nativeEvent,nativeEvent,preventDefault:vi.fn(),stopPropagation:vi.fn()})
  expect(shortcut().props.value).toBe(formatVoiceShortcut('Control+Alt+D'))
  expect(bridge.configure).not.toHaveBeenCalled()
  await save()
  expect(bridge.configure).toHaveBeenCalledExactlyOnceWith({enabled:false,shortcut:'Control+Alt+D'})
})

it('explains a missing backend route through English, Chinese, English without replaying reads or saving',async () => {
  fetchMock.mockResolvedValueOnce(Response.json({code:'route/not-found',message:'missing /api/v1/voice/dictation'},{status:404}))
  render(); await vi.waitFor(() => expect(markup()).toContain(t('ui.voice.backendMismatch')))
  for(const locale of ['en','zh-CN','en'] as const) {
    await setLocale(locale)
    expect(markup()).toContain(t('ui.voice.backendMismatch'))
    expect(markup()).not.toContain('missing /api/v1/voice/dictation')
  }
  expect(fetchMock).toHaveBeenCalledOnce()
  expect(bridge.configure).not.toHaveBeenCalled()
})
