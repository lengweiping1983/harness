import { useEffect, useMemo, useRef, useState } from 'react'
import { latestMemberPreview, type MemberPreviewFrame } from '@/lib/memberPreview'
import { ArrowDown } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import { useAppStore } from '@/store/app'
import { useChatStore } from '@/store/chat'
import { TeamMemberDetails } from '@/components/chat/blocks/TeamMemberDetails'

type Target = NonNullable<ReturnType<typeof useAppStore.getState>['inspectorMemberTarget']>

export function MemberDetailPanel({ target, active }: { target: Target; active: boolean }) {
  useLocale()
  const scroll = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  const [atBottom, setAtBottom] = useState(true)
  useEffect(() => {
    const node = scroll.current, body = content.current
    if (!active || !node || !body) return
    const observer = new ResizeObserver(() => {
      if (following.current) node.scrollTop = Math.max(0, node.scrollHeight - node.clientHeight)
    })
    observer.observe(body)
    return () => observer.disconnect()
  }, [active])
  const assignment = useChatStore(state => {
    const blocks = state.sessions[target.owner]?.blocks ?? []
    for (let index = blocks.length - 1; index >= 0; index--) {
      const block = blocks[index]!
      if (block.kind !== 'team') continue
      const member = block.members.find(row => row.agent_id === target.id || row.planning_agent_id === target.id)
      if (member) return block.assignments?.[member.name] || (member.role === 'lead' ? block.task : '') || ''
    }
    return ''
  })
  const latest = useChatStore(state => active ? latestMemberPreview(state.sessions[target.owner]?.blocks, target.id) : null)
  const retained = useRef<MemberPreviewFrame | null>(null)
  if (active) retained.current = latest
  const frame = active ? latest : retained.current
  const preview = useMemo(() => frame ? { text: frame.text, thinking: frame.thinking, truncated: frame.truncated, live: frame.status === 'streaming' } : target.preview, [frame, target.preview])
  return <section className="flex h-full min-h-0 flex-col" aria-label={target.name}>
    {assignment && <details className="group shrink-0 border-b border-line px-4 py-3">
      <summary className="cursor-pointer list-none text-xs text-muted">
        <span className="font-medium text-ink">{target.leadName || t('ui.team.author.lead')} · {t('ui.member.assignedTask')}</span>
        <span className="mt-1 line-clamp-2 group-open:hidden">{assignment}</span>
      </summary>
      <p className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap break-words text-sm text-ink">{assignment}</p>
    </details>}
    <div className="relative min-h-0 flex-1">
    <div ref={scroll} onScroll={() => {
      const node = scroll.current
      if (!active || !node) return
      following.current = node.scrollHeight - node.clientHeight - node.scrollTop < 24
      setAtBottom(following.current)
    }} className="h-full overflow-y-auto p-4">
      <div ref={content}>
      <TeamMemberDetails owner={target.owner} id={target.id} name={target.name} agent={target.agent} role={target.role} leadName={target.leadName} leadAgent={target.leadAgent} contextSessionId={target.contextSessionId} preview={preview} embedded active={active} />
      </div>
    </div>
    {!atBottom && <button type="button" className="absolute bottom-3 right-4 inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-2 text-xs text-ink shadow-sm hover:bg-raised focus-visible:outline-brand" onClick={() => {
      following.current = true
      setAtBottom(true)
      if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight
    }}><ArrowDown className="h-3.5 w-3.5" />{t('ui.member.latest')}</button>}
    </div>
  </section>
}
