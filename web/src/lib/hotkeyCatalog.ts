import { t } from '@/i18n'
import { isMac } from '@/lib/utils'
/** 主修饰键使用 Mod，终端等字面 Control 组合保持 Ctrl。 */
export const HOTKEYS = {
  newSession: 'Mod+Shift+U',
  conversationSearch: 'Mod+F',
  sessionSearch: '/',
  commandPalette: 'Mod+K',
  settings: 'Mod+,',
  shortcutHelp: 'Shift+/',
  sidebar: 'Mod+B',
  inspector: 'Mod+J',
  theme: 'Mod+Shift+D',
  files: 'Mod+\\',
  quickOpen: 'Mod+P',
  terminal: 'Ctrl+`',
  browser: 'Ctrl+Shift+`',
  git: 'Mod+Shift+G',
  tools: 'Mod+1',
  todos: 'Mod+2',
  context: 'Mod+3',
  exportSession: 'Mod+Shift+E',
  screenshot: 'Mod+Shift+S',
  screenshotHidden: 'Mod+Shift+H',
} as const

export function formatHotkey(keys: string, mac = isMac()): string {
  return keys.split('+').map(key => key === 'Mod' ? (mac ? '⌘' : 'Ctrl') : key).join('+')
}

export function matchesNewSessionHotkey(event: Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey' | 'shiftKey' | 'altKey'>): boolean {
  return matchesHotkey(event, HOTKEYS.newSession)
}

export function matchesHotkey(event: Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey' | 'shiftKey' | 'altKey'>, chord: string): boolean {
  const keys = chord.split('+')
  return event.key.toLowerCase() === keys[keys.length - 1].toLowerCase() &&
    (isMac() ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey) &&
    event.shiftKey === keys.includes('Shift') && !event.altKey
}

export interface HotkeyRow {
  group: string
  label: string
  keys: string
}

export const HOTKEY_ROWS: HotkeyRow[] = [
  { get group() { return t('ui.lib.hotkeyCatalog.a63280253f17') }, get label() { return t('ui.lib.triggerSources.58e21b8737cc') }, keys: HOTKEYS.newSession },
  { get group() { return t('ui.lib.hotkeyCatalog.a63280253f17') }, get label() { return t("ui.components.overlays.ConversationSearch.589e8a71ea91") }, keys: HOTKEYS.conversationSearch },
  { get group() { return t('ui.lib.hotkeyCatalog.a63280253f17') }, get label() { return t('ui.components.overlays.CommandPalette.feb95554de09') }, keys: HOTKEYS.sessionSearch },
  { get group() { return t('ui.lib.hotkeyCatalog.a63280253f17') }, get label() { return t('ui.components.sidebar.ProjectSidebar.ac8b027cc1e3') }, keys: HOTKEYS.exportSession },
  { get group() { return t('ui.hotkeys.application') }, get label() { return t("ui.lib.hotkeyCatalog.4b495d8b87b8") }, keys: HOTKEYS.commandPalette },
  { get group() { return t('ui.hotkeys.application') }, get label() { return t("ui.components.overlays.SettingsDialog.df3d58c7d84b") }, keys: HOTKEYS.settings },
  { get group() { return t('ui.hotkeys.application') }, get label() { return t("ui.lib.hotkeyCatalog.fd938ad1645d") }, keys: HOTKEYS.shortcutHelp },
  { get group() { return t("ui.desktop.menu.view") }, get label() { return t("ui.lib.hotkeyCatalog.9b0083f8421f") }, keys: HOTKEYS.sidebar },
  { get group() { return t("ui.desktop.menu.view") }, get label() { return t("ui.lib.hotkeyCatalog.4e5ac32751f9") }, keys: HOTKEYS.inspector },
  { get group() { return t("ui.desktop.menu.view") }, get label() { return t("ui.lib.hotkeyCatalog.7a1604323e8b") }, keys: HOTKEYS.theme },
  { get group() { return t("ui.desktop.menu.view") }, get label() { return t("ui.lib.hotkeyCatalog.dbb897d77e76") }, keys: HOTKEYS.files },
  { get group() { return t("ui.desktop.menu.view") }, get label() { return t("ui.quickOpen.title") }, keys: HOTKEYS.quickOpen },
  { get group() { return t("ui.desktop.menu.view") }, get label() { return t("ui.components.inspector.Inspector.e19c3b9e266c") }, keys: HOTKEYS.browser },
  { get group() { return t("ui.desktop.menu.view") }, get label() { return t("ui.worktrees.heading") }, keys: HOTKEYS.git },
  { get group() { return t("ui.desktop.menu.view") }, get label() { return t("ui.lib.hotkeyCatalog.b31edc5504cf") }, keys: HOTKEYS.terminal },
  { get group() { return t("ui.desktop.menu.view") }, get label() { return t("ui.settings.tools.title") }, keys: HOTKEYS.tools },
  { get group() { return t("ui.desktop.menu.view") }, get label() { return t("ui.inspector.plansAndTodos") }, keys: HOTKEYS.todos },
  { get group() { return t("ui.desktop.menu.view") }, get label() { return t("ui.lib.hotkeyCatalog.6fc38ef3602f") }, keys: HOTKEYS.context },
  { get group() { return t("ui.lib.hotkeyCatalog.2087c777c06f") }, get label() { return t("ui.lib.hotkeyCatalog.edecf0ae6e51") }, keys: 'Enter' },
  { get group() { return t("ui.lib.hotkeyCatalog.2087c777c06f") }, get label() { return t('ui.components.chat.ScreenshotButton.c95dc99afe57') }, keys: HOTKEYS.screenshot },
  { get group() { return t("ui.lib.hotkeyCatalog.2087c777c06f") }, get label() { return t('ui.components.chat.ScreenshotButton.092645d6b89a') }, keys: HOTKEYS.screenshotHidden },
  { get group() { return t("ui.lib.hotkeyCatalog.2087c777c06f") }, get label() { return t("ui.lib.hotkeyCatalog.bd609a8e2d40") }, keys: 'Shift+Enter' },
  { get group() { return t("ui.lib.hotkeyCatalog.2087c777c06f") }, get label() { return t("ui.lib.hotkeyCatalog.fc1013035d3c") }, keys: '@/ /' },
  { get group() { return t("ui.lib.hotkeyCatalog.1ad1463fe16f") }, get label() { return t("ui.lib.hotkeyCatalog.9ad0aac32ea3") }, keys: 'Esc' },
]
