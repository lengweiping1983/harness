import { t } from '@/i18n'
import type { ResourceCopy } from '@/types'
export interface BrowserActivity {
  id: string
  action: string
  actor: 'agent' | 'user'
  status: 'running' | 'waiting_dialog' | 'unconfirmed' | 'completed' | 'failed' | 'cancelled'
  started_at: number
  ended_at?: number
  error?: string
  takeover_requested?: boolean
}
export interface BrowserState {
  viewer?: { fast_frames: boolean; resize: boolean; hover: boolean; navigation?: boolean; takeover?: boolean }
  navigation?: { phase: 'requesting' | 'loading' | 'interactive' | 'complete' | 'stopped' | 'failed' }
  dialog?: BrowserDialog | null
  operation_pending?: boolean
  enabled?: boolean
  started: boolean
  owner: 'agent' | 'user'
  control_revision: number
  active_tab: string | null
  tabs: { id: string; url: string; title: string }[]
  viewport: { width: number; height: number }
  activity: BrowserActivity | null
  history: BrowserActivity[]
  health?: { errors: number; warnings: number; total: number; dropped: number }
}

export interface BrowserDialog {
  id: string
  page_id: string
  type: 'alert' | 'confirm' | 'prompt' | 'beforeunload'
  message: string
  message_copy?: ResourceCopy
  url: string
  default_value?: string
  response_unconfirmed: boolean
  origin_action?: Pick<BrowserActivity, 'id' | 'action' | 'actor' | 'status'>
}
export function browserActivityStatus(status: BrowserActivity['status']) {
  return { running: t("ui.lib.browser.dc9591e56d50"), waiting_dialog: t("ui.lib.browser.055ffcdb6504"), unconfirmed: t("ui.lib.browser.c098e854e60b"), completed: t("ui.lib.browser.c0b3fbff51cc"), failed: t("ui.lib.browser.28384d7afd2e"), cancelled: t("ui.lib.browser.2cd0f3be8738") }[status]
}
export const browserActionLabel: Record<string, string> = Object.setPrototypeOf({
  get navigate() { return t("ui.lib.browser.82d9ce3a5b37") }, get observe() { return t("ui.lib.browser.6edad06e296c") }, get screenshot() { return t("ui.lib.browser.996d469f0b02") }, get click() { return t("ui.lib.browser.dc71485f8ee9") }, get fill() { return t("ui.lib.browser.369a85b919ff") },
  get options() { return t("ui.lib.browser.263c5093b58e") }, get select() { return t("ui.lib.browser.558f727c56df") }, get check() { return t("ui.lib.browser.421c18e1e962") }, get press() { return t("ui.lib.browser.7c7577d4d4fc") }, get type() { return t("ui.components.chat.blocks.ToolBlock.2087c777c06f") }, get scroll() { return t("ui.lib.browser.34dedaffd3cc") }, get back() { return t("browser.desktop.back") }, get forward() { return t("browser.desktop.forward") }, get reload() { return t("ui.components.chat.QueuedFollowups.aee887434131") },
  get resize() { return t("ui.lib.browser.d0e01ad3aaa3") }, get move() { return t("ui.lib.browser.624c767a3360") }, get stop_loading() { return t("browser.desktop.stop") }, get diagnostics() { return t("ui.lib.browser.9f423fc77190") }, get dialog() { return t("ui.lib.browser.b59d84873888") }, get tabs() { return t("ui.lib.browser.09112ce55ba5") }, get new_tab() { return t("browser.desktop.addTab") }, get select_tab() { return t("ui.lib.browser.68ae5f427c42") }, get close_tab() { return t("browser.desktop.closeTab") },
}, null)

export interface BrowserDiagnostic {
  id: number
  kind: 'console' | 'javascript' | 'http' | 'request'
  level: 'error' | 'warning'
  message: string
  url: string
  timestamp: number
  line?: number
  status?: number
  resource_type?: string
}
export interface BrowserDiagnostics {
  page_id: string | null
  revision: number
  summary: { errors: number; warnings: number; total: number; dropped: number }
  records: BrowserDiagnostic[]
  page_content_trusted: false
}
