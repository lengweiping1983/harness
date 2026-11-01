import { t, useLocale } from '@/i18n'
import { useEffect, useState } from 'react'
import { DeferredPanel } from '@/components/ui/DeferredPanel'
import { loadTerminalWorkspace, loadBrowserWorkspace } from './panelLoaders'

type WorkspaceProps = { sessionId: string | null; active: boolean }
const loaders = {terminal: loadTerminalWorkspace, browser: loadBrowserWorkspace}

/** 已访问工作台保留实例，模块超时与迟到恢复共用检查器加载边界。 */
export function DeferredWorkspace({ kind, sessionId, active }: WorkspaceProps & { kind: keyof typeof loaders }) {
  useLocale()
  const [opened, setOpened] = useState(false)
  useEffect(() => { if (active) setOpened(true) }, [active])
  if (!active && !opened) return null
  return <DeferredPanel load={loaders[kind]} panelProps={{sessionId, active}}
    label={t(kind === 'terminal' ? 'ui.components.inspector.Inspector.e2a76ef1f12e' : 'ui.components.inspector.Inspector.e19c3b9e266c')} />
}
