import { t, useLocale } from '@/i18n'
import { memo } from 'react'
import { CheckCircle2, ChevronRight, Circle, CircleDot, ListChecks } from 'lucide-react'
import type { TodoItem } from '@/types'
import { cn } from '@/lib/utils'

/** Native disclosure stays closed initially and keeps its state across task updates. */
export const TaskDock = memo(function TaskDock({ todos }: { todos: TodoItem[] }) {
  useLocale()

  if (!todos.length) return null
  const done = todos.filter((todo) => todo.status === 'completed').length
  const current = todos.find((todo) => todo.status === 'in_progress')
  const label = current?.content || (done === todos.length ? t("ui.components.chat.TaskDock.950c6a5548ba") : t("ui.components.chat.TaskDock.43a6fee40da0"))
  return (
    <details className="composer-tasks group/task min-w-0 rounded-lg" aria-label={t("ui.components.chat.TaskDock.a4ea7d95390b")}>
      <summary title={label} className="flex min-h-8 cursor-pointer list-none items-center gap-2 rounded-lg px-2 py-1 text-xs text-muted hover:bg-raised [&::-webkit-details-marker]:hidden focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand">
        <ListChecks className="h-3.5 w-3.5 shrink-0 text-brand" aria-hidden="true" />
        <span className="shrink-0 font-medium text-ink">{t("ui.components.chat.TaskDock.a4ea7d95390b")}</span>
        <span className="composer-task-preview min-w-0 flex-1 truncate" title={label}>{label}</span>
        <span className="shrink-0 tabular-nums" aria-label={t("ui.components.chat.TaskDock.9259ff7d6f7c", {p0: (done), p1: (todos.length)})}>{done}/{todos.length}</span>
        <ChevronRight className="h-3.5 w-3.5 shrink-0 transition-transform group-open/task:rotate-90" aria-hidden="true" />
      </summary>
      <ul className="max-h-queue-compact space-y-1 overflow-y-auto border-t border-line px-3 py-2" aria-label={t("ui.components.chat.TaskDock.2a2b6bde4c75")}>
        {todos.map((todo, index) => {
          const active = todo.status === 'in_progress', complete = todo.status === 'completed'
          const Icon = complete ? CheckCircle2 : active ? CircleDot : Circle
          return <li key={todo.id ?? index} className="flex items-start gap-2 py-1 text-xs leading-5">
            <Icon className={cn('mt-0.5 h-3.5 w-3.5 shrink-0', complete ? 'text-ok' : active ? 'text-brand' : 'text-faint')} aria-hidden="true" />
            <span className="sr-only">{complete ? t("ui.components.chat.TaskDock.d097506c04c2") : active ? t("ui.components.chat.TaskDock.eda42c87835f") : t("ui.components.chat.TaskDock.467da41a8aa7")}</span>
            <span className={cn('min-w-0 break-words', complete ? 'text-faint line-through' : 'text-ink')}>{todo.content || t("ui.components.chat.TaskDock.e23ff8b80f62")}</span>
          </li>
        })}
      </ul>
    </details>
  )
})
