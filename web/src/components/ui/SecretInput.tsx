import {useState, type InputHTMLAttributes} from 'react'
import {Eye, EyeOff, Loader2} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import {cn} from '@/lib/utils'
import {Button} from './primitives'

type SecretInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> & {
  containerClassName?: string
  revealed?: boolean
  onToggleReveal?: () => void
  concealedType?: 'password' | 'text'
  toggleDisabled?: boolean
  toggleBusy?: boolean
  showLabel?: string
  hideLabel?: string
}

export function SecretInput({containerClassName, className, revealed, onToggleReveal, concealedType = 'password', toggleDisabled, toggleBusy, showLabel, hideLabel, disabled, ...input}: SecretInputProps) {
  useLocale()
  const [shown, setShown] = useState(false)
  const visible = revealed ?? shown
  const label = visible ? hideLabel ?? t('ui.secrets.hide') : showLabel ?? t('ui.secrets.show')
  return <span className={cn('relative block min-w-0', containerClassName)} onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget)) setShown(false)
  }}>
    <input {...input} disabled={disabled} type={visible ? 'text' : concealedType} className={cn('ui-field w-full', className, 'pr-11')} />
    <Button variant="ghost" size="sm" className="absolute inset-y-1 right-1 h-auto w-8 px-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand focus-visible:-outline-offset-2"
      aria-label={label} aria-pressed={visible} aria-controls={input.id} title={label} disabled={disabled || toggleDisabled || toggleBusy}
      onMouseDown={event => event.preventDefault()} onClick={event => {event.stopPropagation(); if (onToggleReveal) onToggleReveal(); else setShown(!shown)}}>
      {toggleBusy ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : visible ? <EyeOff aria-hidden className="h-4 w-4" /> : <Eye aria-hidden className="h-4 w-4" />}
    </Button>
  </span>
}
