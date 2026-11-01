import type { ButtonHTMLAttributes } from 'react'
import { PanelLeftClose, PanelLeftOpen, PanelRightClose, SquarePlus, Maximize, Minimize } from 'lucide-react'
import { cn } from '@/lib/utils'

const ICONS = {
  'collapse-left': PanelLeftClose,
  'expand-left': PanelLeftOpen,
  'collapse-right': PanelRightClose,
  'new-tab': SquarePlus,
  expand: Maximize,
  restore: Minimize,
}

/** 工作区栏控件共用尺寸、线宽及交互样式。 */
export function WorkspaceAction({ action, label, className, ...props }: Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> & {
  action: keyof typeof ICONS
  label: string
}) {
  const Icon = ICONS[action]
  return <button type="button" aria-label={label} title={label} className={cn('workspace-action', className)} {...props}>
    <Icon aria-hidden="true" size={16} strokeWidth={1.75} />
  </button>
}
