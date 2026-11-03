import {useEffect, useRef, useState} from 'react'
import {t, useLocale} from '@/i18n'
import {useAppStore} from '@/store/app'

type WindowsChrome = {
  platform: string
  popupMenu: (request: {key: string; x: number; y: number}) => Promise<void>
  setTitleBarAppearance: (appearance: {color: string; symbolColor: string}) => Promise<void>
}
const menuKeys = ['file', 'edit', 'view', 'window', 'help'] as const

/** Windows 菜单与系统按钮共用首行，菜单动作仍由原生应用菜单处理。 */
export function WindowsTitleBar() {
  const desktop = (window as unknown as {harnessDesktop?: WindowsChrome}).harnessDesktop
  if (desktop?.platform !== 'win32') return null
  return <TitleBar desktop={desktop} />
}

function TitleBar({desktop}: {desktop: WindowsChrome}) {
  useLocale()
  const booted = useAppStore(state => state.booted)
  const bar = useRef<HTMLElement>(null)
  const priorFocus = useRef<HTMLElement | null>(null)
  const opening = useRef(false)
  const [selected, setSelected] = useState(0)
  const [active, setActive] = useState<string | null>(null)
  useEffect(() => {
    const root = document.documentElement
    const hex = (value: string) => '#' + value.trim().split(/\s+/).map(part => Number(part).toString(16).padStart(2, '0')).join('')
    const update = () => {
      const style = getComputedStyle(root)
      void desktop.setTitleBarAppearance({color: hex(style.getPropertyValue('--c-rail')), symbolColor: hex(style.getPropertyValue('--c-ink'))})
        .catch(error => console.error(t('ui.desktop.menuSyncFailed'), error))
    }
    update()
    const observer = new MutationObserver(update)
    observer.observe(root, {attributes: true, attributeFilter: ['class']})
    return () => observer.disconnect()
  }, [desktop])
  useEffect(() => {
    let altOnly = false
    const focusMenu = () => {
      if (!booted) return
      if (bar.current?.contains(document.activeElement)) {priorFocus.current?.focus(); return}
      priorFocus.current = document.activeElement as HTMLElement | null
      bar.current?.querySelector<HTMLButtonElement>('button[tabindex="0"]')?.focus()
    }
    const down = (event: KeyboardEvent) => {
      if (event.isComposing || event.keyCode === 229) {altOnly = false; return}
      if (event.key === 'Escape' && bar.current?.contains(document.activeElement)) {event.preventDefault(); event.stopPropagation(); priorFocus.current?.focus(); return}
      altOnly = event.key === 'Alt' && !event.ctrlKey && !event.shiftKey && !event.metaKey
      if (event.key === 'F10' && !event.shiftKey && !event.ctrlKey && !event.altKey && !event.metaKey) {event.preventDefault(); focusMenu()}
    }
    const up = (event: KeyboardEvent) => {
      if (event.key === 'Alt' && altOnly && !event.isComposing && event.keyCode !== 229) {event.preventDefault(); focusMenu()}
      altOnly = false
    }
    window.addEventListener('keydown', down, true)
    window.addEventListener('keyup', up)
    return () => {window.removeEventListener('keydown', down, true); window.removeEventListener('keyup', up)}
  }, [booted])
  const open = async (key: string, button: HTMLButtonElement) => {
    if (opening.current || !booted) return
    opening.current = true
    setActive(key)
    const bounds = button.getBoundingClientRect()
    if (bar.current?.contains(document.activeElement)) priorFocus.current?.focus()
    try {await desktop.popupMenu({key, x: Math.round(bounds.left), y: Math.round(bounds.bottom)})}
    catch (error) {console.error(t('ui.desktop.menuSyncFailed'), error)}
    finally {opening.current = false; setActive(null)}
  }
  return <header ref={bar} className="windows-titlebar">
    <span className="windows-titlebar-brand" aria-hidden="true"><img src="/brand/harness.svg" alt="" /></span>
    <nav role="menubar" aria-label="Harness" onKeyDown={event => {
      if (event.nativeEvent.isComposing || event.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
      let index = selected
      if (event.key === 'ArrowRight') index = (selected + 1) % menuKeys.length
      else if (event.key === 'ArrowLeft') index = (selected + menuKeys.length - 1) % menuKeys.length
      else if (event.key === 'Home') index = 0
      else if (event.key === 'End') index = menuKeys.length - 1
      else if (event.key === 'Escape') {event.preventDefault(); priorFocus.current?.focus(); return}
      else return
      event.preventDefault()
      setSelected(index)
      bar.current?.querySelectorAll<HTMLButtonElement>('button')[index]?.focus()
    }}>
      {menuKeys.map((key, index) => <button key={key} type="button" role="menuitem" aria-haspopup="menu" aria-expanded={active === key} disabled={!booted} tabIndex={selected === index ? 0 : -1}
        onMouseDown={event => event.preventDefault()} onFocus={event => {
          if (event.relatedTarget instanceof HTMLElement && !bar.current?.contains(event.relatedTarget)) priorFocus.current = event.relatedTarget
          setSelected(index)
        }} onClick={event => void open(key, event.currentTarget)} onKeyDown={event => {
          if (event.nativeEvent.isComposing || event.keyCode === 229) {event.preventDefault(); return}
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {event.preventDefault(); void open(key, event.currentTarget)}
        }}>{t('ui.desktop.menu.' + key)}</button>)}
    </nav>
  </header>
}
