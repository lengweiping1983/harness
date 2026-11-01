import {forwardRef, type ComponentProps, type HTMLAttributes, type TextareaHTMLAttributes} from 'react'
import {ArrowUp} from 'lucide-react'
import {Button} from '@/components/ui/primitives'
import {cn} from '@/lib/utils'
import {t} from '@/i18n'

/** 输入区的页面结构、编辑器和发送按钮仅维护一份。 */
export const ComposerDock = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(function ComposerDock({children, className, onKeyDown, ...props}, ref) {
  return <div role="region" aria-label={t('ui.components.chat.Composer.c94b64ba3bd6')} tabIndex={0} {...props} ref={ref} className={cn('composer-dock relative pb-2 pt-1 backdrop-blur-md', className)} onKeyDown={event => {
    onKeyDown?.(event)
    // 仅处理输入区自身的滚动快捷键，编辑器和披露控件保留原来的键盘归属。
    if (event.defaultPrevented || event.target !== event.currentTarget || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229
        || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return
    const region = event.currentTarget
    const target = event.key === 'Home' ? 0 : event.key === 'End' ? region.scrollHeight
      : event.key === 'PageUp' ? region.scrollTop - region.clientHeight * .9
      : event.key === 'PageDown' ? region.scrollTop + region.clientHeight * .9 : null
    if (target === null) return
    event.preventDefault()
    region.scrollTop = target
  }}>
    <div className="mx-auto w-full px-3 sm:px-6" style={{maxWidth: 'var(--composer-card-max-width)'}}>{children}</div>
  </div>
})

export const ComposerCard = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement> & {disconnected?: boolean}>(function ComposerCard({disconnected = false, className, ...props}, ref) {
  return <div {...props} ref={ref} data-disconnected={disconnected ? '1' : '0'} className={cn('composer-card group relative flex flex-col overflow-hidden rounded-composer bg-surface', disconnected && 'opacity-95', className)} />
})

export const ComposerEditor = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function ComposerEditor({className, rows = 1, spellCheck = false, ...props}, ref) {
  return <textarea aria-label={t('ui.components.chat.Composer.a410452649b3')} {...props} ref={ref} rows={rows} spellCheck={spellCheck} className={cn('composer-editor no-scrollbar block w-full resize-none bg-transparent px-5 pb-2 pt-4 text-base leading-reading text-ink placeholder:text-faint focus:outline-none', className)} />
})

export function ComposerToolbar({className, ...props}: HTMLAttributes<HTMLDivElement>) {
  return <div {...props} className={cn('flex flex-wrap items-center gap-1.5 px-3 pb-3 pt-1', className)} />
}

export function ComposerSendButton({className, ...props}: ComponentProps<typeof Button>) {
  return <Button type="button" aria-label={t('ui.components.chat.Composer.edecf0ae6e51')} title={t('ui.components.chat.Composer.edecf0ae6e51')} {...props} size="md" variant="primary" className={cn('h-9 w-9 rounded-full p-0', className)}><ArrowUp className="h-4.5 w-4.5" /></Button>
}
