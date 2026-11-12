import { t, useLocale } from '@/i18n'
import { useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'

/**
 * JSON / 任意值的树形查看器。
 *
 * 不用第三方库：工具输入输出的形状千变万化，自己渲染反而更好控制默认值、
 * 深度截断与超大数组的折叠策略（一次只展开前 N 项，避免卡顿）。
 */

const MAX_DEPTH_AUTO = 2
const TEXT_PAGE = 600
const MAX_ITEMS = 200

type Kind = 'object' | 'array' | 'string' | 'number' | 'boolean' | 'null' | 'undefined'

function kindOf(value: unknown): Kind {
  if (value === null) return 'null'
  if (Array.isArray(value)) return 'array'
  const type = typeof value
  if (type === 'object') return 'object'
  if (type === 'string' || type === 'number' || type === 'boolean' || type === 'undefined') return type
  return 'string'
}

const PRIMITIVE_CLASS: Record<string, string> = {
  string: 'json-string',
  number: 'json-number',
  boolean: 'json-literal',
  null: 'text-faint',
  undefined: 'text-faint',
}

function Pager({ page, pages, start, end, total, path, onPage }: {
  page: number; pages: number; start: number; end: number; total: number; path: string; onPage: (page: number) => void
}) {
  useLocale()
  return <nav aria-label={t('ui.json.pages', { path })} className="my-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
    <span className="tabular-nums">{t('ui.json.range', { start, end, total })}</span>
    {([['first', 0, page === 0], ['previous', page - 1, page === 0], ['next', page + 1, page >= pages - 1], ['last', pages - 1, page >= pages - 1]] as const).map(([action, target, disabled]) =>
      <button key={action} type="button" disabled={disabled} aria-label={t(`ui.json.${action}For`, { path })}
        className="rounded border border-line px-2 py-1 text-xs disabled:opacity-50 focus-visible:outline-brand"
        onClick={() => onPage(target)}>{t(`ui.json.${action}`)}</button>)}
  </nav>
}

function Scalar({ value, kind, path = '$', bare = false }: { value: unknown; kind: Kind; path?: string; bare?: boolean }) {
  useLocale()
  const characters = useMemo(() => typeof value === 'string' ? Array.from(value) : [], [value])
  const [snapshot, setSnapshot] = useState({ value, page: 0 })
  if (!Object.is(snapshot.value, value)) setSnapshot({ value, page: 0 })
  const page = Object.is(snapshot.value, value) ? snapshot.page : 0
  if (kind === 'string') {
    const pages = Math.max(1, Math.ceil(characters.length / TEXT_PAGE))
    const start = page * TEXT_PAGE
    const end = Math.min(characters.length, (page + 1) * TEXT_PAGE)
    return <div className="min-w-0 max-w-full">
      {pages > 1 ? <Pager page={page} pages={pages} start={start + 1} end={end} total={characters.length} path={path}
        onPage={next => setSnapshot({ value, page: next })} /> : null}
      <span className="whitespace-pre-wrap break-all font-mono text-xs">
        {!bare ? <span className="text-faint">"</span> : null}
        <span data-json-text className={bare ? 'text-muted' : 'json-string'}>{characters.slice(start, end).join('')}</span>
        {!bare ? <span className="text-faint">"</span> : null}
      </span>
    </div>
  }
  if (kind === 'null' || kind === 'undefined') {
    return <span className="font-mono text-xs italic text-faint">{kind}</span>
  }
  return <span className={cn('font-mono text-xs', PRIMITIVE_CLASS[kind])}>{String(value)}</span>
}

function Node({
  label,
  value,
  depth,
  defaultOpen,
  path,
  parentSize = 1,
}: {
  label?: string
  value: unknown
  depth: number
  defaultOpen: boolean
  path: string
  parentSize?: number
}) {
  useLocale()

  const kind = kindOf(value)
  const keys = useMemo(() => kind === 'object' ? Object.keys(value as object) : [], [kind, value])
  const size = kind === 'array' ? (value as unknown[]).length : keys.length
  const initialOpen = defaultOpen || depth < MAX_DEPTH_AUTO && size * parentSize <= MAX_ITEMS
  const [snapshot, setSnapshot] = useState({ value, open: initialOpen, page: 0 })
  if (!Object.is(snapshot.value, value)) setSnapshot({ value, open: initialOpen, page: 0 })
  const { open, page } = Object.is(snapshot.value, value) ? snapshot : { open: initialOpen, page: 0 }
  const isBranch = kind === 'object' || kind === 'array'

  if (!isBranch) {
    return (
      <div className="flex min-w-0 flex-wrap items-start gap-2 py-px pl-1 leading-code">
        {label !== undefined ? <Key label={label} /> : null}
        <Scalar value={value} kind={kind} path={path} />
      </div>
    )
  }

  const start = page * MAX_ITEMS
  const end = Math.min(size, start + MAX_ITEMS)
  const pages = Math.max(1, Math.ceil(size / MAX_ITEMS))
  const entries: [string, unknown][] = open ? Array.from({ length: end - start }, (_, position) => {
    const key = kind === 'array' ? String(start + position) : keys[start + position]
    return [key, (value as Record<string, unknown>)[key]]
  }) : []

  return (
    <div className="leading-code">
      <button
        type="button"
        onClick={() => setSnapshot({ value, page, open: !open })}
        aria-expanded={open}
        className="flex min-w-0 w-full items-center gap-1.5 rounded py-px text-left transition-colors hover:bg-raised/60"
      >
        <ChevronRight
          className={cn(
            'h-3 w-3 shrink-0 text-faint transition-transform duration-150',
            open && 'rotate-90',
          )}
        />
        {label !== undefined ? <Key label={label} /> : null}
        <span className="text-faint">
          {kind === 'array' ? '[' + size + ']' : '{' + size + '}'}
        </span>
      </button>
      {open ? (
        <div className="border-line ml-2 border-l pl-3">
          {pages > 1 ? <Pager page={page} pages={pages} start={start + 1} end={end} total={size} path={path}
            onPage={next => setSnapshot({ value, open, page: next })} /> : null}
          {entries.map(([key, item]) => (
            <Node key={key} label={key} value={item} depth={depth + 1} defaultOpen={false} path={path + '.' + key} parentSize={size} />
          ))}
          {entries.length === 0 ? (
            <div className="text-faint py-px font-mono text-xs">{t("ui.components.ui.JsonView.2f8267a89ad5")}</div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function Key({ label }: { label: string }) {
  useLocale()

  return <span className="json-key min-w-0 max-w-full break-all font-mono text-xs font-medium">{label}:</span>
}

export function JsonView({ value, className }: { value: unknown; className?: string }) {
  useLocale()
  const kind = kindOf(value)
  if (kind === 'string') {
    return <div data-json-view className={cn('file-preview-source json-view max-h-json min-w-0 overflow-auto rounded-lg border border-line bg-raised/35 p-3 font-mono text-xs leading-code', className)}>
      <Scalar value={value} kind={kind} bare path="$" />
    </div>
  }
  return <div data-json-view className={cn('file-preview-source json-view max-h-json min-w-0 overflow-auto rounded-lg border border-line bg-raised/35 p-3 font-mono', className)}>
    <Node value={value} depth={0} defaultOpen path="$" />
  </div>
}
