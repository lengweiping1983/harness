import {forwardRef, useMemo, useRef, type ReactNode} from 'react'
import {Check, Play, Save} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import {isMac} from '@/lib/utils'
import {Button} from '@/components/ui/primitives'

type Props = {content: string; dirty: boolean; disabled: boolean; loading: boolean
  options?: ReactNode; onAsk: () => void; onChange: (content: string) => void; onSave: (returnFocus?: boolean) => void; onRun: (check: boolean, returnFocus?: boolean) => void}

/** 执行快捷键只属于源码编辑器；组合输入和长按不产生执行请求。 */
export const AcademyCodeEditor = forwardRef<HTMLTextAreaElement, Props>(function AcademyCodeEditor({content, dirty, disabled, loading, options, onAsk, onChange, onSave, onRun}, ref) {
  useLocale()
  const composing = useRef(false)
  const gutter = useRef<HTMLPreElement | null>(null)
  const lines = useMemo(() => {
    const count = (content.match(/\r\n|\r|\n/g)?.length ?? 0) + 1
    return {count, numbers: Array.from({length: count}, (_, index) => index + 1).join('\n')}
  }, [content])
  const locked = disabled || loading
  const saveShortcut = isMac() ? '⌘S' : 'Ctrl+S'
  const runShortcut = isMac() ? '⌘↵' : 'Ctrl+Enter'
  const checkShortcut = isMac() ? '⌘⇧↵' : 'Ctrl+Shift+Enter'
  return <div className="mt-2 space-y-3" onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}>
    <div className="flex overflow-hidden rounded-lg border border-line bg-canvas focus-within:border-brand focus-within:ring-2 focus-within:ring-brand/30">
      <div aria-hidden="true" data-academy-line-numbers className="pointer-events-none relative shrink-0 overflow-hidden border-r border-line px-2 font-mono text-sm leading-code text-muted">
        <span className="invisible">{lines.count}</span>
        <pre ref={gutter} className="absolute inset-x-0 top-0 py-3 pr-2 text-right font-mono text-sm leading-code">{lines.numbers}</pre>
      </div>
    <textarea ref={ref} aria-label={t('ui.academy.source')} value={content} spellCheck={false} autoCapitalize="off" autoCorrect="off" wrap="off" disabled={disabled}
      className="min-h-56 min-w-0 flex-1 resize-y bg-canvas p-3 font-mono text-sm leading-code focus:outline-none"
      onScroll={event => {if (gutter.current) gutter.current.style.transform = `translateY(-${event.currentTarget.scrollTop}px)`}}
      onChange={event => onChange(event.target.value)} onKeyDown={event => {
        if ((!event.metaKey && !event.ctrlKey) || event.altKey || event.nativeEvent.getModifierState('AltGraph')) return
        const save = event.key.toLowerCase() === 's' && !event.shiftKey
        if (event.key !== 'Enter' && !save) return
        event.preventDefault()
        event.stopPropagation()
        if (locked || composing.current || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || event.repeat) return
        if (save) {if (dirty) onSave(true)}
        else onRun(event.shiftKey, true)
      }} />
    </div>
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t('ui.academy.optionalRun')}>
      <Button size="sm" variant="primary" disabled={locked} icon={<Play className="h-3.5 w-3.5" />} aria-keyshortcuts="Control+Enter Meta+Enter"
        onClick={() => {if (!composing.current) onRun(false)}}>{t(dirty ? 'ui.academy.saveAndRun' : 'ui.academy.run')}</Button>
      <Button size="sm" variant="subtle" disabled={locked} icon={<Check className="h-3.5 w-3.5" />} aria-keyshortcuts="Control+Shift+Enter Meta+Shift+Enter"
        onClick={() => {if (!composing.current) onRun(true)}}>{t(dirty ? 'ui.academy.saveAndCheck' : 'ui.academy.check')}</Button>
      <Button size="sm" variant="ghost" className="academy-assistant-jump" onClick={() => {if (!composing.current) onAsk()}}>{t('ui.academy.askAssistant')}</Button>
      <details data-academy-section="code-options" className="text-xs text-muted"><summary className="cursor-pointer rounded-lg px-2 py-1 focus-visible:outline focus-visible:outline-brand">{t('ui.academy.more')}</summary><div className="mt-2 space-y-2"><Button size="sm" variant="ghost" disabled={!dirty || locked} aria-keyshortcuts="Control+s Meta+s" icon={<Save className="h-3.5 w-3.5" />}
        onClick={() => {if (!composing.current) onSave()}}>{t('ui.academy.save')}</Button><p>{t('ui.academy.editorShortcuts', {save: saveShortcut, run: runShortcut, check: checkShortcut})}</p>{options}</div></details>
    </div>
  </div>
})
