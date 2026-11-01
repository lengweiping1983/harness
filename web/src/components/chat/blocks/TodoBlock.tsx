import { t, useLocale } from '@/i18n'
import { memo } from 'react'
import { CheckCircle2, Circle, CircleDot, ListChecks } from 'lucide-react'
import type { TodoItem } from '@/types'
import { cn } from '@/lib/utils'

/** 待办清单。in_progress 会呼吸，这是"模型正在干哪一步"最直观的信号。 */
export const TodoBlock = memo(function TodoBlock({ todos }: { todos: TodoItem[] }) {
  useLocale()

  if (!todos.length) return null
  const done = todos.filter((t) => t.status === 'completed').length
  const progress = Math.round((done / todos.length) * 100)

  return (
    <section className="border-line bg-raised/35 overflow-hidden rounded-xl border">
      <header className="flex items-center gap-2 border-b border-line/70 px-3 py-2">
        <ListChecks className="h-3.5 w-3.5 text-brand" />
        <span className="text-xs font-medium text-ink">{t("ui.components.chat.blocks.TodoBlock.a4ea7d95390b")}</span>
        <span className="text-2xs text-faint">
          {done}/{todos.length}
        </span>
        <div className="ml-2 h-1 w-20 overflow-hidden rounded-full bg-line">
          <div
            className="h-full rounded-full bg-brand transition-width duration-500 ease-swift"
            style={{ width: progress + '%' }}
          />
        </div>
      </header>
      <ul className="divide-line/60 divide-y">
        {todos.map((todo, index) => (
          <TodoRow key={todo.id ?? index} todo={todo} />
        ))}
      </ul>
    </section>
  )
})

function TodoRow({ todo }: { todo: TodoItem }) {
  useLocale()

  const status = todo.status ?? 'pending'
  const active = status === 'in_progress'
  const Icon = status === 'completed' ? CheckCircle2 : active ? CircleDot : Circle

  return (
    <li
      className={cn(
        'flex items-start gap-2.5 px-3 py-1.5 transition-colors',
        active && 'bg-brand/[0.06]',
      )}
    >
      <Icon
        className={cn(
          'mt-1 h-3.5 w-3.5 shrink-0',
          status === 'completed'
            ? 'text-ok'
            : active
              ? 'animate-pulse text-brand'
              : 'text-faint/70',
        )}
      />
      <span
        className={cn(
          'min-w-0 flex-1 text-compact leading-relaxed',
          status === 'completed' ? 'text-faint line-through' : 'text-ink',
        )}
      >
        {todo.content ?? t("ui.components.chat.blocks.TodoBlock.419af10c207a")}
      </span>
    </li>
  )
}
