import { t, useLocale } from '@/i18n'
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronRight, Copy, Loader2, RefreshCw } from 'lucide-react'
import { cn, copyText } from '@/lib/utils'
import {handleTablistKeyDown} from '@/lib/tabNavigation'
import { Tooltip } from './Tooltip'
import {useDialogFocus} from '@/hooks/useDialogFocus'

// Tooltip 是自己的模块，但语义上属于 UI 原语，调用方统一从 primitives 取。
export { Tooltip } from './Tooltip'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'subtle'
type Size = 'xs' | 'sm' | 'md' | 'lg'

const VARIANTS: Record<Variant, string> = {
  primary:
    'btn-primary text-brand-ink disabled:opacity-40',
  secondary:
    'bg-raised text-ink border border-line hover:border-line-strong hover:bg-raised/70',
  ghost: 'text-muted hover:text-ink hover:bg-raised',
  subtle: 'bg-brand/10 text-brand hover:bg-brand/15',
  danger: 'bg-danger/[0.12] text-danger hover:bg-danger/20 border border-danger/25',
}

const SIZES: Record<Size, string> = {
  xs: 'h-6 px-2 text-2xs gap-1 rounded-md',
  sm: 'h-8 px-2.5 text-xs gap-1.5 rounded-lg',
  md: 'h-9 px-3.5 text-compact gap-2 rounded-lg',
  lg: 'h-11 px-5 text-sm gap-2 rounded-lg',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  loading?: boolean
  icon?: ReactNode
  iconRight?: ReactNode
  /** 选中态。用于「切换型」按钮（例如窄屏抽屉开关），与 IconButton 同名同义。 */
  active?: boolean
}

export function Button({
  variant = 'secondary',
  size = 'md',
  loading,
  icon,
  iconRight,
  className,
  children,
  disabled,
  active,
  ...rest
}: ButtonProps) {
  useLocale()

  return (
    <button
      type="button"
      disabled={disabled || loading}
      className={cn(
        'inline-flex select-none items-center justify-center whitespace-nowrap font-medium',
        'transition-all duration-150 ease-swift active:scale-press',
        'disabled:pointer-events-none disabled:opacity-55',
        VARIANTS[variant],
        SIZES[size],
        active && 'bg-brand/[0.12] text-brand border-brand/30',
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : icon}
      {children}
      {iconRight}
    </button>
  )
}

export function ShowMoreControl({ children, className, disabled, expanded = false, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { expanded?: boolean }) {
  useLocale()
  return <button type="button" disabled={disabled} className={cn('activity-summary disabled:cursor-default disabled:opacity-55', className)} {...rest}>
    <ChevronRight aria-hidden className={cn('h-3.5 w-3.5 shrink-0 text-faint transition-transform', expanded && 'rotate-90')} />
    <span>{children ?? t('ui.showMore')}</span>
  </button>
}

export interface RefreshButtonProps extends Omit<ButtonProps, 'children' | 'icon' | 'iconRight'> {
  label: string
  spinning?: boolean
}

export function RefreshButton({ label, spinning = false, className, variant = 'ghost', ...rest }: RefreshButtonProps) {
  useLocale()
  const visibleLabel = t('ui.worktrees.refresh')
  const accessibleLabel = label === visibleLabel ? label : visibleLabel + ' · ' + label
  return <Button {...rest} variant={variant} aria-label={accessibleLabel} title={label} className={cn('refresh-action shrink-0', className)} icon={<RefreshCw aria-hidden className={cn('h-3.5 w-3.5 shrink-0', spinning && 'animate-spin')} />}>
    <span className="refresh-action-label">{visibleLabel}</span>
  </Button>
}

/* ============================ IconButton + Tooltip ============================ */

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  variant?: Variant
  size?: Size
  active?: boolean
  children: ReactNode
}

export function IconButton({
  label,
  variant = 'ghost',
  size = 'sm',
  active,
  className,
  children,
  ...rest
}: IconButtonProps) {
  useLocale()

  return (
    <Tooltip content={label}>
      <button
        type="button"
        aria-label={label}
        className={cn(
          'inline-flex select-none items-center justify-center transition-all duration-150 ease-swift',
          'active:scale-95 disabled:pointer-events-none disabled:opacity-50',
          VARIANTS[variant],
          SIZES[size],
          active && 'bg-brand/[0.12] text-brand',
          className,
        )}
        {...rest}
      >
        {children}
      </button>
    </Tooltip>
  )
}


/* ============================ Kbd ============================ */

export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  useLocale()

  return <kbd className={cn('kbd', className)}>{children}</kbd>
}

/* ============================ Spinner ============================ */

export function Spinner({ className }: { className?: string }) {
  useLocale()

  return <Loader2 className={cn('h-3.5 w-3.5 animate-spin text-brand', className)} />
}

export function Dots({ className }: { className?: string }) {
  useLocale()

  return (
    <span className={cn('inline-flex items-center gap-1', className)}>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1 w-1 animate-blink rounded-full bg-current"
          style={{ animationDelay: `${i * 0.14}s` }}
        />
      ))}
    </span>
  )
}

/* ============================ Badge ============================ */

export type Tone = 'neutral' | 'brand' | 'ok' | 'warn' | 'danger'

const TONES: Record<Tone, string> = {
  neutral: 'bg-raised text-muted border-line',
  brand: 'bg-brand/[0.12] text-brand border-brand/25',
  ok: 'bg-ok/[0.12] text-ok border-ok/25',
  warn: 'bg-warn/[0.12] text-warn border-warn/25',
  danger: 'bg-danger/[0.12] text-danger border-danger/25',
}

export function Badge({
  tone = 'neutral',
  children,
  icon,
  className,
  title,
}: {
  tone?: Tone
  children: ReactNode
  icon?: ReactNode
  className?: string
  title?: string
}) {
  useLocale()

  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-2xs font-medium',
        TONES[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  )
}

/* ============================ Segmented ============================ */

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
  icon?: ReactNode
  title?: string
  badge?: ReactNode
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  className,
  size = 'sm',
}: {
  options: SegmentedOption<T>[]
  value: T
  onChange: (value: T) => void
  className?: string
  size?: 'sm' | 'md'
}) {
  useLocale()

  return (
    <div
      role="tablist"
      onKeyDown={handleTablistKeyDown}
      className={cn(
        // A recessed trough with a hairline ring, so the active pill reads as
        // a physical key lifted out of the track rather than a colour swap.
        'inline-flex items-center gap-0.5 rounded-xl border border-line bg-raised p-1',
        'shadow-inset',
        className,
      )}
    >
      {options.map((option) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            role="tab"
            type="button"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            title={option.title}
            onClick={() => onChange(option.value)}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-lg font-medium transition-all duration-150 ease-swift',
              size === 'sm' ? 'h-7 px-2 text-2xs' : 'h-8 px-3 text-xs',
              active
                ? [
                    'bg-surface text-ink',
                    'shadow-control',
                  ]
                : 'text-faint hover:bg-surface/55 hover:text-muted',
            )}
          >
            {option.icon}
            {option.label}
            {option.badge}
          </button>
        )
      })}
    </div>
  )
}

/* ============================ CopyButton ============================ */

export function CopyButton({
  text,
  contentIdentity,
  label = t("ui.components.ui.primitives.63d90d977348"),
  size = 'xs',
  className,
}: {
  label?: string
  size?: Size
  className?: string
} & ({ text: string; contentIdentity?: never } | { text: () => string; contentIdentity: unknown })) {
  useLocale()

  const [done, setDone] = useState(false)
  const [failed, setFailed] = useState(false)
  const locked = useRef(false)
  const mounted = useRef(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const generation = useRef(0)
  const identity = typeof text === 'string' ? text : contentIdentity

  useLayoutEffect(() => {
    generation.current++
    clearTimeout(timer.current)
    setDone(false)
    setFailed(false)
  }, [identity])

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; clearTimeout(timer.current) }
  }, [])

  const run = async () => {
    if (locked.current) return
    locked.current = true
    const version = generation.current
    clearTimeout(timer.current)
    setDone(false); setFailed(false)
    try {
      const ok = await copyText(typeof text === 'function' ? text() : text)
      if (!mounted.current || version !== generation.current) return
      setFailed(!ok)
      setDone(ok)
      if (ok) timer.current = setTimeout(() => setDone(false), 1600)
    } catch {
      if (mounted.current && version === generation.current) setFailed(true)
    } finally { locked.current = false }
  }

  const feedback = failed ? t('ui.components.chat.blocks.MessageActions.753d8bb0da99')
    : done ? t('ui.components.ui.primitives.8f6f8d979c98') : ''
  return (
    <>
    <Button
      size={size}
      variant="ghost"
      onClick={run}
      aria-label={label}
      title={feedback || label}
      className={className}
      icon={done ? <Check className="h-3.5 w-3.5 text-ok" /> : <Copy className="h-3.5 w-3.5" />}
    >
      {feedback || null}
    </Button>
    <span role="status" className="sr-only">{feedback}</span>
    </>
  )
}

/* ============================ 遮罩 ============================ */

export function Overlay({
  onClose,
  onEscape,
  children,
  className,
  labelledBy,
}: {
  onClose: () => void
  onEscape?: (event: KeyboardEvent) => boolean
  children: ReactNode
  className?: string
  labelledBy?: string
}) {
  useLocale()

  const dialogRef = useRef<HTMLDivElement | null>(null)
  useDialogFocus(dialogRef, true, onClose, onEscape)

  return createPortal(
    <div
      className="overlay-scrim fixed inset-0 z-overlay flex overflow-y-auto overscroll-contain animate-fade-in motion-reduce:animate-none items-start justify-center p-4 sm:p-8"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby={labelledBy}
        className={cn(
          'overlay-panel relative w-full shrink-0 overflow-hidden rounded-2xl bg-surface',
          'animate-pop-in motion-reduce:animate-none',
          className,
        )}
      >
        {children}
      </div>
    </div>,
    document.body,
  )
}

/* ============================ 通用容器 ============================ */

export function Section({
  title,
  right,
  children,
  className,
}: {
  title?: ReactNode
  right?: ReactNode
  children: ReactNode
  className?: string
}) {
  useLocale()

  return (
    <section className={cn('space-y-2', className)}>
      {title ? (
        <header className="flex items-center justify-between gap-2 px-0.5">
          <h3 className="ui-group-title">
            {title}
          </h3>
          {right}
        </header>
      ) : null}
      {children}
    </section>
  )
}

export function EmptyHint({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode
  title: string
  description?: string
  action?: ReactNode
  className?: string
}) {
  useLocale()

  return (
    <div
      className={cn(
        'relative flex flex-col items-center justify-center gap-2 overflow-hidden',
        'rounded-xl px-4 py-10 text-center',
        className,
      )}
    >
      {/* 顶部一层极淡的品牌氛围光，避免空态像一块死掉的占位框 */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 -top-10 h-24"
        style={{
          background:
            'radial-gradient(closest-side, rgb(var(--c-brand-400) / 0.10) 0%, transparent 100%)',
        }}
      />
      {icon ? (
        <div className="relative grid h-9 w-9 place-items-center rounded-full border border-line bg-raised/60 text-faint shadow-soft">
          {icon}
        </div>
      ) : null}
      <p className="relative text-xs font-medium tracking-wide text-muted">
        {title}
      </p>
      {description ? (
        <p className="relative max-w-copy text-2xs leading-relaxed text-faint">
          {description}
        </p>
      ) : null}
      {action ? <div className="relative">{action}</div> : null}
    </div>
  )
}

export function Switch({ className, label, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'role'> & {label?: string}) {
  return <input {...props} aria-label={props['aria-label'] ?? label} type="checkbox" role="switch" className={cn(
    'relative inline-block h-5 w-9 shrink-0 cursor-pointer appearance-none rounded-full border border-line-strong bg-raised align-middle transition-colors checked:border-brand checked:bg-brand before:absolute before:left-0.5 before:top-0.5 before:h-3.5 before:w-3.5 before:rounded-full before:bg-ink before:shadow before:transition-transform checked:before:translate-x-4 checked:before:bg-brand-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-canvas disabled:cursor-not-allowed disabled:opacity-50',
    className,
  )} />
}
