import { useEffect, useRef, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { Button } from '@/components/ui/primitives'

/** 确认绑定父级 key 对应的删除目标；失败保留确认，由业务层回查未知结果。 */
export function ConfirmRemoval({ name, prompt, disabled, onConfirm }: {
  name: string; prompt: string; disabled: boolean; onConfirm: () => Promise<boolean>
}) {
  useLocale()
  const [confirming, setConfirming] = useState(false)
  const [pending, setPending] = useState(false)
  const locked = useRef(false)
  const root = useRef<HTMLDivElement>(null)
  const mounted = useRef(false)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const entered = useRef(false)
  useEffect(() => {
    if (confirming) { entered.current = true; root.current?.querySelector<HTMLButtonElement>('[data-cancel]')?.focus() }
    else if (entered.current) root.current?.querySelector<HTMLButtonElement>('[data-trigger]')?.focus()
  }, [confirming])
  const remove = async () => {
    if (disabled || locked.current) return
    locked.current = true
    setPending(true)
    try {
      if (await onConfirm() && mounted.current) setConfirming(false)
    } finally {
      locked.current = false
      if (mounted.current) setPending(false)
    }
  }
  return <div ref={root} className="min-w-0">{!confirming ? <Button data-trigger="" size="xs" variant="ghost" disabled={disabled} aria-label={t('ui.catalog.removeNamed', { name })} onClick={() => setConfirming(true)}><Trash2 className="h-3 w-3" />{t('ui.catalog.remove')}</Button> : <div role="group" aria-label={prompt} className="flex max-w-sm flex-wrap items-center gap-2 rounded-lg border border-line bg-raised p-2">
    <p className="w-full text-xs text-muted [overflow-wrap:anywhere]">{prompt}</p>
    <Button size="xs" disabled={disabled || pending} onClick={() => void remove()}>{t('ui.catalog.confirmRemove')}</Button>
    <Button data-cancel="" size="xs" variant="secondary" disabled={disabled || pending} onClick={() => setConfirming(false)}>{t('ui.memory.cancelDelete')}</Button>
  </div>}</div>
}
