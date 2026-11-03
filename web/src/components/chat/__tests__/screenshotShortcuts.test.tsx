import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import type {ReactElement} from 'react'
import {ScreenshotButton} from '../ScreenshotButton'
import {ScreenshotCrop} from '../ScreenshotCrop'
import {useDesktopMenu} from '@/hooks/useDesktopMenu'
import {useHotkeys} from '@/hooks/useHotkeys'
import {useAppStore} from '@/store/app'
import {setLocale, t} from '@/i18n'
import {HOTKEYS, HOTKEY_ROWS, formatHotkey} from '@/lib/hotkeyCatalog'
import {requestScreenshot} from '@/lib/screenshotCommand'
import {noticeText} from '@/lib/noticeText'

const hooks = vi.hoisted(() => ({cursor:0, slots:[] as any[], cleanups:new Map<number, () => void>()}))
const capture = vi.hoisted(() => ({capabilities:vi.fn()}))
vi.mock('react', async original => {
  const effect = (run:() => void | (() => void), deps?:unknown[]) => {
    const index = hooks.cursor++, previous = hooks.slots[index] as unknown[] | undefined
    if (!deps || !previous || deps.some((dep, i) => dep !== previous[i])) {
      hooks.cleanups.get(index)?.(); hooks.cleanups.delete(index)
      hooks.slots[index] = deps
      const cleanup = run(); if (cleanup) hooks.cleanups.set(index, cleanup)
    }
  }
  return {...await original<typeof import('react')>(),
    useRef:(value:unknown) => {const index = hooks.cursor++; return hooks.slots[index] ??= {current:value}},
    useState:(value:unknown) => {const index = hooks.cursor++; if (!(index in hooks.slots)) hooks.slots[index] = value
      return [hooks.slots[index], (next:unknown) => {hooks.slots[index] = next}]},
    useId:() => 'screenshot-menu', useEffect:effect, useLayoutEffect:effect,
    useSyncExternalStore:(_subscribe:unknown, snapshot:() => unknown) => snapshot(),
  }
})
vi.mock('@/i18n', async original => {const module = await original<typeof import('@/i18n')>(); return {...module, useLocale:module.getLocale}})
vi.mock('@/store/app', async original => {
  const module = await original<typeof import('@/store/app')>()
  return {...module, useAppStore:Object.assign((selector:(state:ReturnType<typeof module.useAppStore.getState>) => unknown) => selector(module.useAppStore.getState()), module.useAppStore)}
})
vi.mock('react-dom', () => ({createPortal:(node:unknown) => node}))
vi.mock('@/hooks/useAnchoredMenu', () => ({useAnchoredMenu:() => ({visibility:'hidden'})}))
vi.mock('@/lib/api', async original => ({...await original<typeof import('@/lib/api')>(), api:{screenshotCapabilities:capture.capabilities}}))

const initialApp = useAppStore.getState()
let keys:Set<(event:KeyboardEvent) => void>, commands:Set<(action:string) => void>
let screenshot:ReturnType<typeof vi.fn>, labels:ReturnType<typeof vi.fn>, addFiles:ReturnType<typeof vi.fn>, focus:ReturnType<typeof vi.fn>, modal:boolean
function render() {
  hooks.cursor = 0
  useDesktopMenu(); useHotkeys()
  return ScreenshotButton({addFiles, focusEditor:focus})
}
function find(node:unknown, predicate:(element:ReactElement<any>) => boolean):ReactElement<any> | undefined {
  if (!node || typeof node !== 'object') return
  if (Array.isArray(node)) return node.map(child => find(child, predicate)).find(Boolean)
  const element = node as ReactElement<any>
  return predicate(element) ? element : find(element.props?.children, predicate)
}
function press(overrides:Partial<KeyboardEvent> = {}) {
  const event = {key:'S', code:'KeyS', metaKey:true, ctrlKey:false, shiftKey:true, altKey:false,
    target:{tagName:'TEXTAREA', closest:() => null}, getModifierState:() => false,
    preventDefault:vi.fn(), ...overrides} as unknown as KeyboardEvent
  keys.forEach(handler => handler(event))
  return event
}
function unmount() {Array.from(hooks.cleanups.values()).reverse().forEach(cleanup => cleanup()); hooks.cleanups.clear()}
beforeEach(() => {
  hooks.slots = []; hooks.cleanups.clear(); keys = new Set(); commands = new Set(); modal = false
  screenshot = vi.fn(async () => null); labels = vi.fn(async () => {}); addFiles = vi.fn(async () => {}); focus = vi.fn()
  capture.capabilities.mockReset().mockResolvedValue({ok:true, data:{native:true, hideWindow:true, reason:null}})
  vi.stubGlobal('window', {harnessDesktop:{screenshot, setMenu:labels, onCommand:(handler:(action:string) => void) => {commands.add(handler); return () => commands.delete(handler)}},
    addEventListener:(_name:string, handler:(event:KeyboardEvent) => void) => keys.add(handler), removeEventListener:(_name:string, handler:(event:KeyboardEvent) => void) => keys.delete(handler)})
  vi.stubGlobal('document', {hidden:false, title:'Harness', body:{}, documentElement:{}, querySelector:() => modal ? {} : null,
    addEventListener:vi.fn(), removeEventListener:vi.fn()})
  vi.stubGlobal('navigator', {platform:'MacIntel'})
  useAppStore.setState({booted:true, activeSessionId:'capture-a', newSessionRevision:1, settingsOpen:false,
    shortcutsOpen:false, commandOpen:false, conversationSearchOpen:false, quickOpen:false, lightboxSrc:null, workspacePickerOpen:false})
})
afterEach(async () => {unmount(); await Promise.resolve(); useAppStore.setState(initialApp); vi.unstubAllGlobals(); await setLocale('en')})

it.each(['MacIntel', 'Win32', 'Linux x86_64'])('captures once from an editable field on %s and ignores IME, modifiers and repeats', async platform => {
  vi.stubGlobal('navigator', {platform})
  render()
  const modifier = platform === 'MacIntel' ? {metaKey:true, ctrlKey:false} : {metaKey:false, ctrlKey:true}
  for (const invalid of [{isComposing:true}, {keyCode:229}, {repeat:true}, {shiftKey:false}, {altKey:true}, {metaKey:true, ctrlKey:true}]) press({...modifier, ...invalid})
  expect(screenshot).not.toHaveBeenCalled()
  press(modifier); press(modifier)
  expect(screenshot).toHaveBeenCalledExactlyOnceWith('capture', false)
  await vi.waitFor(() => expect(screenshot).toHaveBeenCalledWith('finish'))
  expect(focus).toHaveBeenCalledOnce()
})

it('routes native menu and hidden capture shortcuts through the same busy operation', async () => {
  let complete!:(value:null) => void
  screenshot.mockImplementation((action:string) => action === 'capture' ? new Promise(resolve => {complete = resolve}) : Promise.resolve(null))
  render()
  press({key:'H', code:'KeyH'})
  commands.forEach(handler => {handler('screenshot'); handler('screenshot-hidden')})
  expect(screenshot).toHaveBeenCalledExactlyOnceWith('capture', true)
  complete(null)
  await vi.waitFor(() => expect(focus).toHaveBeenCalledOnce())
  commands.forEach(handler => handler('screenshot'))
  expect(screenshot.mock.calls.filter(([action]) => action === 'capture')).toEqual([['capture', true], ['capture', false]])
  complete(null)
  await vi.waitFor(() => expect(focus).toHaveBeenCalledTimes(2))
})

it('keeps native menu, tooltip and both shortcut help rows localized in one instance', async () => {
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    await setLocale(locale)
    const tree = render()
    expect(labels.mock.calls.at(-1)?.[0].screenshot).toBe(t('ui.components.chat.ScreenshotButton.c95dc99afe57'))
    expect(labels.mock.calls.at(-1)?.[0].screenshotHidden).toBe(t('ui.components.chat.ScreenshotButton.092645d6b89a'))
    expect(find(tree, element => element.props?.content)?.props.content).toBe(t('ui.screenshot.shortcutHint', {shortcut:formatHotkey(HOTKEYS.screenshot)}))
    for (const chord of [HOTKEYS.screenshot, HOTKEYS.screenshotHidden]) expect(HOTKEY_ROWS.filter(row => row.keys === chord)).toHaveLength(1)
  }
  expect(screenshot).not.toHaveBeenCalled()
  expect(capture.capabilities).toHaveBeenCalledOnce()
})

it.each(['dialog', 'settings', 'picker', 'hidden', 'unmount'] as const)('does not capture from a %s input area', mode => {
  render()
  if (mode === 'dialog') modal = true
  if (mode === 'settings') useAppStore.setState({settingsOpen:true})
  if (mode === 'picker') useAppStore.setState({workspacePickerOpen:true})
  if (mode === 'hidden') Object.assign(document, {hidden:true})
  if (mode === 'unmount') unmount()
  press(); commands.forEach(handler => handler('screenshot'))
  expect(requestScreenshot()).toBe(false)
  expect(screenshot).not.toHaveBeenCalled()
})

it('uses the existing crop confirmation once and waits for cleanup before accepting another command', async () => {
  screenshot.mockImplementation((action:string) => Promise.resolve(action === 'capture' ? {url:'harness-screenshot://capture/test'} : null))
  render(); press()
  let crop!:ReactElement<any>
  await vi.waitFor(() => {crop = find(render(), element => element.type === ScreenshotCrop)!; expect(crop).toBeDefined()})
  commands.forEach(handler => handler('screenshot-hidden'))
  expect(screenshot).toHaveBeenCalledExactlyOnceWith('capture', false)
  const file = new File(['image'], 'capture.png', {type:'image/png'})
  await crop.props.onConfirm(file)
  await vi.waitFor(() => expect(focus).toHaveBeenCalledOnce())
  expect(addFiles).toHaveBeenCalledExactlyOnceWith([file])
  expect(screenshot.mock.calls).toEqual([['capture', false], ['finish']])
})

it.each(['switch', 'round-trip', 'new-draft', 'unmount'] as const)('finishes a late capture after %s without adding files or stealing focus', async mode => {
  let complete!:(value:{url:string}) => void
  screenshot.mockImplementation((action:string) => action === 'capture' ? new Promise(resolve => {complete = resolve}) : Promise.resolve(null))
  render(); press()
  if (mode === 'switch' || mode === 'round-trip') useAppStore.setState({activeSessionId:'capture-b'})
  if (mode === 'round-trip') useAppStore.setState({activeSessionId:'capture-a'})
  if (mode === 'new-draft') useAppStore.setState({newSessionRevision:2})
  if (mode === 'unmount') unmount()
  complete({url:'harness-screenshot://capture/test'})
  await vi.waitFor(() => expect(screenshot).toHaveBeenCalledWith('finish'))
  expect(addFiles).not.toHaveBeenCalled(); expect(focus).not.toHaveBeenCalled()
  if (mode !== 'unmount') expect(find(render(), element => element.type === ScreenshotCrop)).toBeUndefined()
})

it('waits for cleanup and keeps failed cleanup locked for menu and keyboard entry', async () => {
  let reject!:(reason:Error) => void
  screenshot.mockImplementation((action:string) => action === 'finish' ? new Promise((_resolve, fail) => {reject = fail}) : Promise.resolve(null))
  render(); press()
  await vi.waitFor(() => expect(screenshot).toHaveBeenCalledWith('finish'))
  press(); commands.forEach(handler => handler('screenshot-hidden'))
  expect(screenshot).toHaveBeenCalledTimes(2)
  reject(Error('restore failed 原文'))
  await vi.waitFor(() => expect(useAppStore.getState().toasts.at(-1)?.title).toEqual({key:'ui.screenshot.cleanupFailed'}))
  render(); press(); commands.forEach(handler => handler('screenshot'))
  expect(screenshot).toHaveBeenCalledTimes(2); expect(focus).not.toHaveBeenCalled()
})

it('keeps permission guidance bilingual and lets the next explicit click authorize again', async () => {
  screenshot.mockImplementation((action:string) => Promise.resolve(action === 'capture' ? {permission:{key:'ui.systemPermission.screen.settings'}} : null))
  render(); press()
  await vi.waitFor(() => expect(focus).toHaveBeenCalledOnce())
  const notice = useAppStore.getState().toasts.at(-1)!
  expect(notice.title).toEqual({key:'ui.systemPermission.required'})
  expect(notice.description).toEqual({key:'ui.systemPermission.screen.settings'})
  for (const locale of ['en','zh-CN','en'] as const) {
    await setLocale(locale); render()
    expect(noticeText(notice.title)).toBe(t('ui.systemPermission.required'))
    expect(noticeText(notice.description!)).toBe(t('ui.systemPermission.screen.settings'))
  }
  expect(screenshot.mock.calls.filter(([action]) => action === 'capture')).toHaveLength(1)
  expect(addFiles).not.toHaveBeenCalled()
  press()
  await vi.waitFor(() => expect(focus).toHaveBeenCalledTimes(2))
  expect(screenshot.mock.calls.filter(([action]) => action === 'capture')).toHaveLength(2)
})

it('consumes localized native failure receipts and treats restoration failures as locked', async () => {
  screenshot.mockImplementation((action:string) => Promise.resolve({failure:{key:action === 'capture' ? 'ui.screenshot.captureFailed' : 'ui.screenshot.cleanupUnknown'}}))
  render(); press()
  await vi.waitFor(() => expect(useAppStore.getState().toasts.at(-1)?.title).toEqual({key:'ui.screenshot.cleanupFailed'}))
  const notices = useAppStore.getState().toasts
  expect(notices.at(-2)?.description).toEqual({key:'ui.screenshot.captureFailed'})
  for (const locale of ['en','zh-CN','en'] as const) {
    await setLocale(locale); render()
    expect(noticeText(notices.at(-2)!.description!)).toBe(t('ui.screenshot.captureFailed'))
  }
  press()
  expect(screenshot).toHaveBeenCalledTimes(2)
  expect(focus).not.toHaveBeenCalled()
})

it('discards delayed permission guidance after a conversation round-trip', async () => {
  let complete!:(value:unknown) => void
  screenshot.mockImplementation((action:string) => action === 'capture' ? new Promise(resolve => {complete = resolve}) : Promise.resolve(null))
  render(); press()
  useAppStore.setState({activeSessionId:'capture-b'}); useAppStore.setState({activeSessionId:'capture-a'})
  const previous = useAppStore.getState().toasts.length
  complete({permission:{key:'ui.systemPermission.screen.settings'}})
  await vi.waitFor(() => expect(screenshot).toHaveBeenCalledWith('finish'))
  expect(useAppStore.getState().toasts).toHaveLength(previous)
  expect(focus).not.toHaveBeenCalled()
})
