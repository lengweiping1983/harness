import {useEffect} from 'react'
import {t, useLocale} from '@/i18n'
import {useAppStore} from '@/store/app'

type DesktopMenu = {
  setMenu: (labels: Record<string, string>) => Promise<void>
  onCommand: (listener: (action: string) => void) => () => void
}

export function useDesktopMenu() {
  const locale = useLocale()
  const booted = useAppStore(state => state.booted)
  useEffect(() => {
    const desktop = (window as unknown as {harnessDesktop?: DesktopMenu}).harnessDesktop
    if (!desktop?.setMenu || !booted) return
    const keys = ['about','settings','hide','hideOthers','unhide','quit','file','newChat','close','edit','undo','redo','cut','copy','paste','selectAll','view','commands','sidebar','inspector','files','terminal','browser','fullscreen','window','minimize','zoom','front','help','shortcuts','checkUpdates','updateDevelopment','updateCurrent','updateAvailable','updateDownloaded','updateFailed','downloadUpdate','later','ok','updateAvailableMenu','openProject','updateCheckingMenu','updateDownloadingMenu','updateReadyMenu','unsavedChanges','keepEditing','discardLeave','contextOpenLink','contextCopyLink','contextInspect']
    void desktop.setMenu(Object.fromEntries(keys.map(key => [key, t('ui.desktop.menu.' + key)]))).catch(error => console.error(t('ui.desktop.menuSyncFailed'), error))
  }, [locale, booted])
  useEffect(() => {
    const desktop = (window as unknown as {harnessDesktop?: DesktopMenu}).harnessDesktop
    return desktop?.onCommand?.(action => {
      const state = useAppStore.getState()
      if (!state.booted) return
      switch (action) {
        case 'new-chat': void state.startNewSession(); break
        case 'open-project': state.setWorkspacePickerOpen(true); break
        case 'settings': state.setSettingsOpen(true); break
        case 'commands': state.setCommandOpen(true); break
        case 'sidebar': state.toggleSidebar(); break
        case 'inspector': state.toggleInspector(); break
        case 'files': case 'terminal': case 'browser': state.setInspectorTab(action); break
        case 'shortcuts': state.setShortcutsOpen(true); break
      }
    })
  }, [])
}
