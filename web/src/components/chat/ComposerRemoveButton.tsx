import type { ButtonHTMLAttributes } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

/** 输入区标签共享移除入口；触屏常显，鼠标悬停和键盘聚焦时显现。 */
export function ComposerRemoveButton({ className, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button {...props} type="button" className={cn(
    'absolute right-1 top-1 inline-flex h-5 w-5 items-center justify-center rounded-md bg-surface text-muted',
    'opacity-0 pointer-events-none group-hover/composer-item:opacity-100 group-hover/composer-item:pointer-events-auto group-focus-within/composer-item:opacity-100 group-focus-within/composer-item:pointer-events-auto pointer-coarse:opacity-100 pointer-coarse:pointer-events-auto',
    'hover:bg-raised hover:text-danger focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-brand disabled:pointer-events-none',
    className,
  )}>{children ?? <X className="h-3 w-3" aria-hidden="true" />}</button>
}
