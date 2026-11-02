import { t, useLocale } from '@/i18n'
import { StrictMode } from 'react'
import { DeferredPanel } from './components/ui/DeferredPanel'
import { createRoot } from 'react-dom/client'
import {takeSharedToken} from './lib/sharedAccess'
import {readInvitationRecovery} from './lib/invitationRecovery'
import {loadApp} from './lib/appLoader'
import {WindowsTitleBar} from './components/desktop/WindowsTitleBar'

/**
 * 入口。
 *
 * 主题在 `index.html` 里已经先落了一版（防白闪），这里只做 React 接管。
 * 渲染用 `createRoot` + StrictMode：开发期暴露副作用问题，
 * 生产构建 React 18 会自动去掉双调用。
 */

const container = document.getElementById('root')
if (!container) throw new Error(t("ui.main.f3053dc1dc0b"))

const loadShared = () => import('./components/shared/SharedSessionPage').then(module => ({default: module.SharedSessionPage}))

const loadInvitation = () => import('./components/shared/InvitationPage').then(module => ({default: module.InvitationPage}))

const shared = window.location.pathname === '/shared'
const initialToken = shared ? takeSharedToken() || readInvitationRecovery()?.token || '' : ''

function Page() {
  useLocale()
  const label = t('ui.deferred.page.name')
  const fallbackClassName = 'flex min-h-dvh flex-col items-center justify-center gap-3 bg-surface p-4 text-sm text-muted'
  return shared ? initialToken.startsWith('hi1.')
    ? <DeferredPanel load={loadInvitation} panelProps={{token: initialToken}} label={label} fallbackClassName={fallbackClassName} />
    : <DeferredPanel load={loadShared} panelProps={{initialToken}} label={label} fallbackClassName={fallbackClassName} />
    : <DeferredPanel load={loadApp} panelProps={{}} label={label} fallbackClassName={fallbackClassName} />
}

createRoot(container).render(<StrictMode><WindowsTitleBar /><Page /></StrictMode>)
