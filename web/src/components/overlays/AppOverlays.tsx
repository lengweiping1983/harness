import {useEffect, useId, useRef, useState, type ComponentType} from 'react'
import {t, useLocale} from '@/i18n'
import {useAppStore} from '@/store/app'
import {DeferredPanel} from '@/components/ui/DeferredPanel'
import {Overlay} from '@/components/ui/primitives'
import {SettingsDialog} from './SettingsDialog'

const loaders = {
  commandOpen: () => import('./CommandPalette').then(module => ({default: module.CommandPalette})),
  shortcutsOpen: () => import('./CommandPalette').then(module => ({default: module.ShortcutsDialog})),
  conversationSearchOpen: () => import('./ConversationSearch').then(module => ({default: module.ConversationSearch})),
  quickOpen: () => import('./QuickOpen').then(module => ({default: module.QuickOpen})),
  workspacePickerOpen: () => import('./WorkspacePicker').then(module => ({default: module.WorkspacePicker})),
  lightboxSrc: () => import('../markdown/ImageLightbox').then(module => ({default: module.ImageLightbox})),
} satisfies Record<string, () => Promise<{default: ComponentType}>>

const close = {
  commandOpen: () => useAppStore.getState().setCommandOpen(false),
  shortcutsOpen: () => useAppStore.getState().setShortcutsOpen(false),
  conversationSearchOpen: () => useAppStore.getState().setConversationSearchOpen(false),
  quickOpen: () => useAppStore.getState().setQuickOpen(false),
  workspacePickerOpen: () => useAppStore.getState().setWorkspacePickerOpen(false),
  lightboxSrc: () => useAppStore.getState().closeLightbox(),
}

function OnFirstOpen({name}: {name: keyof typeof loaders}) {
  useLocale()
  const open = useAppStore(state => Boolean(state[name]))
  const [requested, setRequested] = useState(false)
  const headingId = useId()
  const fallback = useRef<HTMLDivElement>(null)
  const origin = useRef<HTMLElement | null>(null)
  const wasOpen = useRef(false)
  if (open && !wasOpen.current) origin.current = document.activeElement as HTMLElement | null
  wasOpen.current = open
  useEffect(() => {if (open) setRequested(true)}, [open])
  // 首次打开后保留组件实例，草稿与已受理写入继续沿原组件生命周期收尾。
  if (!open && !requested) return null
  const label = t('ui.deferred.page.name')
  return <DeferredPanel load={loaders[name]} panelProps={{}} label={label}
    onReady={() => {
      // 真正浮层渲染前恢复原焦点，避免它把即将卸载的加载按钮记成返回目标。
      if (origin.current?.isConnected && fallback.current?.parentElement?.contains(document.activeElement)) origin.current.focus()
    }}
    renderFallback={content => open ? <Overlay onClose={close[name]} labelledBy={headingId} className="max-w-lg p-4">
      <div ref={fallback}>
        <h2 id={headingId} className="sr-only">{label}</h2>
        {content}
        <button type="button" onClick={close[name]} className="mt-2 rounded-lg px-2 py-1 text-sm text-brand hover:bg-raised">{t('ui.deferred.close')}</button>
      </div>
    </Overlay> : null} />
}

export function AppOverlays() {
  return <><SettingsDialog />{(Object.keys(loaders) as (keyof typeof loaders)[]).map(name => <OnFirstOpen key={name} name={name} />)}</>
}
