import { BookOpen } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { useAppStore } from '@/store/app'

/** 教学入口只切换工作区，不创建对话或改动当前运行。 */
export function LearnHarnessButton({topic}: {topic: 'tool' | 'request'}) {
  useLocale()
  return <button type="button" className="inline-flex min-h-8 items-center gap-1 rounded-lg px-2 text-xs text-brand focus-visible:outline-brand"
    onClick={() => useAppStore.getState().setInspectorTab('academy')}>
    <BookOpen className="h-3.5 w-3.5" aria-hidden="true" />
    {t(topic === 'tool' ? 'ui.academy.learnTool' : 'ui.academy.learnRequest')}
  </button>
}
