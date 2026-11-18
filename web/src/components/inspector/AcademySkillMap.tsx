import {useId, useRef, useState} from 'react'
import {Check} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import type {AcademyLesson, AcademyProject, AcademySkillState} from '@/types'
import {academySkillMap, academySkillMapEdgePath, type AcademySkillMapNode} from '@/lib/academySkillMap'
import {cn} from '@/lib/utils'

/** 技能地图：先修关系图＋FSRS 记忆光环；点击节点复用面板开课写入准入，键盘 Enter/空格等价。 */
export function AcademySkillMap({lessons, progress, skills, activeLessonId, recommendedId, onOpenLesson}: {
  lessons: ReadonlyArray<AcademyLesson>; progress: AcademyProject['progress']; skills: ReadonlyArray<AcademySkillState>
  activeLessonId?: string; recommendedId?: string; onOpenLesson?: (lessonId: string) => void
}) {
  useLocale()
  const markerId = 'academy-map-arrow-' + useId().replace(/:/g, '')
  const containerRef = useRef<HTMLDivElement>(null)
  const [focusId, setFocusId] = useState<string | null>(null)
  const layout = academySkillMap(lessons, progress, skills, activeLessonId, recommendedId)
  const interactive = Boolean(onOpenLesson)
  const activeFocusId = focusId ?? layout.nodes[0]?.id
  /** 空间方向键导航：在指定方向上取最近的节点（横向按 y 近似、纵向按 x 近似），漫游 tabindex 跟随焦点。 */
  const moveFocus = (from: AcademySkillMapNode, key: string) => {
    const horizontal = key === 'ArrowRight' || key === 'ArrowLeft'
    const candidates = layout.nodes.filter(node => {
      if (key === 'ArrowRight') return node.x > from.x
      if (key === 'ArrowLeft') return node.x < from.x
      if (key === 'ArrowDown') return node.y > from.y
      return node.y < from.y
    })
    if (!candidates.length) return
    candidates.sort((a, b) => {
      const perpA = horizontal ? Math.abs(a.y - from.y) : Math.abs(a.x - from.x)
      const perpB = horizontal ? Math.abs(b.y - from.y) : Math.abs(b.x - from.x)
      if (perpA !== perpB) return perpA - perpB
      return horizontal ? Math.abs(a.x - from.x) - Math.abs(b.x - from.x) : Math.abs(a.y - from.y) - Math.abs(b.y - from.y)
    })
    const target = candidates[0]
    setFocusId(target.id)
    requestAnimationFrame(() => {containerRef.current?.querySelector<SVGGElement>('[data-skill-map-node="' + target.id + '"]')?.focus()})
  }
  if (!lessons.length) return <p className="text-xs text-muted">{t('ui.academy.map.empty')}</p>
  return <div ref={containerRef} data-academy-skill-map className="space-y-2">
    <p className="text-xs font-medium">{t('ui.academy.map.title')}</p>
    <p className="text-xs leading-reading text-muted">{t('ui.academy.map.intro')}</p>
    {interactive ? <p className="sr-only">{t('ui.academy.map.keyboardHint')}</p> : null}
    <div className="overflow-x-auto rounded-xl border border-line bg-canvas p-3">
      <svg data-skill-map-canvas viewBox={`0 0 ${layout.width} ${layout.height}`} width="100%" height={layout.height} style={{minWidth: layout.columns * 150}} role="group" aria-label={t('ui.academy.map.title')}>
        <defs>
          <marker id={markerId} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 8 4 L 0 8 z" className="fill-line" />
          </marker>
        </defs>
        {layout.edges.map(edge => <path key={edge.from + '>' + edge.to} data-skill-map-edge d={academySkillMapEdgePath(edge)} fill="none" strokeWidth="1.5" className="stroke-line" markerEnd={`url(#${markerId})`} aria-hidden />)}
        {layout.nodes.map(node => {
          const retention = node.retrievability != null ? Math.round(node.retrievability * 100) : null
          const label = node.title + ' · ' + t('ui.academy.mastery.state.' + (node.mastery ?? 'new'))
            + (retention != null ? ' · ' + t('ui.academy.studio.journey.memory', {percent: retention}) : '')
            + (node.due ? ' · ' + t('ui.academy.map.legend.due') : '')
          return <g key={node.id} data-skill-map-node={node.id} transform={`translate(${node.x},${node.y})`}
            role={interactive ? 'button' : 'img'} tabIndex={interactive ? (node.id === activeFocusId ? 0 : -1) : -1}
            aria-current={node.active ? 'step' : undefined} aria-label={label}
            className={cn('focus-visible:outline focus-visible:outline-brand', interactive && 'cursor-pointer')}
            onClick={interactive ? () => onOpenLesson?.(node.id) : undefined}
            onFocus={interactive ? () => setFocusId(node.id) : undefined}
            onKeyDown={interactive ? event => {
              if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); onOpenLesson?.(node.id); return}
              if (event.key === 'ArrowRight' || event.key === 'ArrowLeft' || event.key === 'ArrowDown' || event.key === 'ArrowUp') {event.preventDefault(); moveFocus(node, event.key)}
            } : undefined}>
            <title>{node.title + (retention != null ? ' · ' + t('ui.academy.studio.journey.memory', {percent: retention}) : '')}</title>
            {retention != null ? <>
              <circle r="21" fill="none" strokeWidth="3" className="stroke-line" />
              <circle data-skill-map-halo r="21" fill="none" strokeWidth="3" strokeLinecap="round"
                className={node.retrievability! >= 0.8 ? 'stroke-ok' : node.retrievability! >= 0.5 ? 'stroke-warn' : 'stroke-danger'}
                strokeDasharray={2 * Math.PI * 21} strokeDashoffset={2 * Math.PI * 21 * (1 - Math.max(0, Math.min(1, node.retrievability!)))} transform="rotate(-90)" />
            </> : null}
            <circle r="16" strokeWidth="2" className={node.completed ? 'fill-ok/10 stroke-ok' : node.active ? 'fill-brand/10 stroke-brand' : 'fill-canvas stroke-line'} />
            {node.completed ? <Check x={-7} y={-7} width={14} height={14} className="text-ok" aria-hidden />
              : <text textAnchor="middle" dy="4" fontSize="12" className="fill-current text-muted">{node.order}</text>}
            <text textAnchor="middle" y="36" fontSize="10" className="fill-current text-muted">{node.title.length > 14 ? node.title.slice(0, 14) + '…' : node.title}</text>
            {node.due ? <circle data-skill-map-due cx="15" cy="-15" r="4" className="fill-warn" /> : null}
            {node.recommended ? <g transform="translate(0,-32)" aria-hidden>
              <rect x="-18" y="-9" width="36" height="14" rx="7" className="fill-brand/15" />
              <text textAnchor="middle" dy="2" fontSize="9" className="fill-brand">{t('ui.academy.studio.journey.recommended')}</text>
            </g> : null}
          </g>
        })}
      </svg>
    </div>
    <div className="flex flex-wrap items-center gap-2 text-xs text-muted" data-skill-map-legend>
      <span className="flex items-center gap-1"><Check className="h-3 w-3 text-ok" aria-hidden />{t('ui.academy.map.legend.completed')}</span>
      <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full border-2 border-warn" aria-hidden />{t('ui.academy.map.legend.learning')}</span>
      <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full border-2 border-ok" aria-hidden />{t('ui.academy.map.legend.durable')}</span>
      <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-warn" aria-hidden />{t('ui.academy.map.legend.due')}</span>
      <span className="flex items-center gap-1 rounded-full bg-brand/15 px-2 text-brand">{t('ui.academy.map.legend.recommended')}</span>
    </div>
  </div>
}
