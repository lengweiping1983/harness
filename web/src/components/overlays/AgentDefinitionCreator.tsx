import type {ResourceText} from '@/types'
import {ResourceError, failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import { Switch } from '@/components/ui/primitives'
import type { AgentSkillSelection, AgentCreatedResponse, AgentUpdatedResponse } from '@/lib/protocol.generated'
import { agentDraftKey, serializeAgentDraft, persistAgentDraft, readAgentDraft, type AgentDraft } from '@/lib/agentDraft'
import { AgentAvatar } from '@/components/ui/AgentAvatar'
import { useEffect, useId, useRef, useState } from 'react'
import { t, useLocale } from '@/i18n'
import { api } from '@/lib/api'
import { download } from '@/lib/utils'
import type { ExternalAgentCatalog } from '@/lib/externalAgents'
import { ExternalRunnerStatus } from './ExternalRunnerStatus'
import { ApiError, isUnconfirmedWrite, pluginRequest, runtimeRequest } from '@/lib/http'
import { Button, Overlay } from '@/components/ui/primitives'
import { Crown, Users, X, Upload } from 'lucide-react'
import { useAppStore } from '@/store/app'
import { useChatStore } from '@/store/chat'
import type { AgentMountState } from '@/lib/agentMount'
import { TeamMemberPicker } from './TeamMemberPicker'
import { AgentSkillPicker } from './AgentSkillPicker'
import { defaultTeamMemberName, memberRenameIssue, resolveMemberRuntime, addExternalTeamMember, updateTeamMember, removeTeamMember, readTeamMembers, teamMemberIssue } from '@/lib/teamAuthoring'

type Agent = { skills?: AgentSkillSelection; name: string; display_name?: string; profession?: string; description: string; agent_type: 'agent' | 'team'; system_prompt?: string; model?: string | null; runner?: 'codex' | 'claude' | 'opencode' | null; metadata?: Record<string, unknown>; members?: Member[]; enabled?: boolean; avatar?: string | null; edit_scope?: 'user' | 'project' | null; edit_revision?: string }
type Member = { skills?: AgentSkillSelection; display_name?: string | null; execution?: 'inherit' | 'model'; name: string; role: string; agent?: string | null; prompt?: string; model?: string | null; depends_on?: string[]; runner?: 'codex' | 'claude' | 'opencode' | null }
const field = 'w-full min-w-0 rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-ink focus:outline-none focus:border-brand/50'
const joinFeedback = (parts: ResourceText[]): ResourceText => parts.reduce<ResourceText>((first, second) => first ? {template: {key: 'ui.common.feedbackSequence', params: {first, second}}} : second, '')
const newCommandId = () => 'agent-' + Array.from(crypto.getRandomValues(new Uint8Array(6)), value => value.toString(16).padStart(2, '0')).join('')
import type { PersonalityProfile as Profile } from '@/lib/protocol.generated'
export function AgentDefinitionCreator({ sessionId, seed, agents, refresh, initialKind = 'agent', viewSeed = null, onCloseView }: { sessionId: string | null; seed: Agent | null; viewSeed?: Agent | null; onCloseView?: () => void; agents: Agent[]; refresh: () => Promise<void>; initialKind?: 'agent' | 'team' }) {
  const locale = useLocale()
  const [open, setOpen] = useState(false)
  const [readOnly, setReadOnly] = useState(false)
  const [name, setName] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [profession, setProfession] = useState('')
  const [generatedName, setGeneratedName] = useState(newCommandId)
  const [description, setDescription] = useState('')
  const [prompt, setPrompt] = useState('')
  const [model, setModel] = useState('')
  const [skills, setSkills] = useState<string[] | null>(null)
  const [runner, setRunner] = useState('')
  const models = useAppStore(state => state.models)
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [profileSource, setProfileSource] = useState<'all' | 'user' | 'builtin'>('all')
  const [profileError, setProfileError] = useState<ResourceText>('')
  const [personalityPrompt, setPersonalityPrompt] = useState<string | null>(null)
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setProfileError('')
    void runtimeRequest<{ profiles: Profile[]; warning?: string }>('/personalities', 'GET', undefined, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) { setProfiles(data.profiles); setProfileError(data.warning ?? '') } })
      .catch(failure => { if (!controller.signal.aborted) setProfileError(failureText(failure)) })
    return () => controller.abort()
  }, [locale, open])
  const [runnerCatalog, setRunnerCatalog] = useState<ExternalAgentCatalog | null>(null)
  const [runnerLoading, setRunnerLoading] = useState(false)
  const [runnerError, setRunnerError] = useState<ResourceText>('')
  const [runnerRevision, setRunnerRevision] = useState(0)
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setRunnerLoading(true); setRunnerError('')
    void api.externalAgents(controller.signal).then(result => {
      if (controller.signal.aborted) return
      if (result.ok) setRunnerCatalog(result.data)
      else setRunnerError(result.copy ?? result.message)
      setRunnerLoading(false)
    })
    return () => controller.abort()
  }, [open, runnerRevision])
  const runnerStatus = (value: string) => <ExternalRunnerStatus key={value} runner={value} catalog={runnerCatalog} loading={runnerLoading} error={noticeText(runnerError)} refresh={() => setRunnerRevision(revision => revision + 1)} />
  const [previewAgent, setPreviewAgent] = useState<Agent | null>(null)
  const [metadataText, setMetadataText] = useState('{}')
  useEffect(() => {
    if (!open || readOnly) return
    setMetadataText(previous => {
      try {
        const value: unknown = JSON.parse(previous)
        if (!value || typeof value !== 'object' || Array.isArray(value)) return previous
        const next = {...value} as Record<string, unknown>
        delete next.display_name; delete next.profession; delete next.runner
        if (displayName.trim()) next.display_name = displayName.trim()
        if (profession.trim()) next.profession = profession.trim()
        if (runner) next.runner = runner
        if (personalityPrompt !== null) next.personality_prompt = personalityPrompt
        else delete next.personality_prompt
        return JSON.stringify(next) === JSON.stringify(value) ? previous : JSON.stringify(next, null, 2)
      } catch { return previous }
    })
  }, [open, readOnly, displayName, profession, runner, personalityPrompt])
  function editMetadata(value: string) {
    setMetadataText(value)
    try {
      const row = JSON.parse(value)
      if (!row || typeof row !== 'object' || Array.isArray(row)) return
      setDisplayName(typeof row.display_name === 'string' ? row.display_name : '')
      setProfession(typeof row.profession === 'string' ? row.profession : '')
      setRunner(typeof row.runner === 'string' ? row.runner : '')
      setPersonalityPrompt(typeof row.personality_prompt === 'string' ? row.personality_prompt : null)
    } catch { /* 保留未完成的 JSON，继续显示校验提示。 */ }
  }
  const [kind, setKind] = useState<'agent' | 'team'>(initialKind)
  const [scope, setScope] = useState<'user' | 'project'>(sessionId ? 'project' : 'user')
  const [members, setMembers] = useState('[]')
  const [memberNameEdits, setMemberNameEdits] = useState<Record<string, string>>({})
  const [useNow, setUseNow] = useState(false)
  const [busy, setBusy] = useState(false)
  const operation = useRef(false)
  const [error, setError] = useState<ResourceText>('')
  const [saveUnconfirmed, setSaveUnconfirmed] = useState(false)
  const [notice, setNotice] = useState<ResourceText>('')
  const [editRevision, setEditRevision] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const titleId = useId()
  const nameInput = useRef<HTMLInputElement>(null)
  const live = useRef(true)
  useEffect(() => { live.current = true; return () => { live.current = false } }, [])
  const [draftKey, setDraftKey] = useState<string | null>(null)
  const [draftNotice, setDraftNotice] = useState<ResourceText>('')
  const [draftError, setDraftError] = useState<ResourceText>('')
  const [retainedDraft, setRetainedDraft] = useState<string | null>(null)
  const draftGeneration = useRef(0)
  const draftBase = useRef<AgentDraft | null>(null)
  const draftBaseline = useRef('')
  const draftPaused = useRef(false)
  const currentDraft: AgentDraft = {version: 5, skills, memberNameEdits, name, displayName, profession, generatedName, description, prompt, model, runner, personalityPrompt, metadataText, members, kind, scope, useNow, editRevision}
  const serializedDraft = serializeAgentDraft(currentDraft)
  function ownsDraft(generation: number) {
    return live.current && draftGeneration.current === generation
  }
  function applyDraft(row: AgentDraft) {
    setName(row.name); setDisplayName(row.displayName); setProfession(row.profession); setGeneratedName(row.generatedName)
    setDescription(row.description); setPrompt(row.prompt); setModel(row.model); setRunner(row.runner)
    setPersonalityPrompt(row.personalityPrompt === null ? null : row.personalityPrompt.trim()); setMetadataText(row.metadataText); setMembers(row.members)
    setMemberNameEdits(row.memberNameEdits ?? {}); setSkills(row.skills ?? null)
    setKind(row.kind); setScope(row.scope); setUseNow(row.useNow); setEditRevision(row.editRevision)
  }
  function openDraft(base: AgentDraft, target: string, inspect = false) {
    draftGeneration.current++
    const key = agentDraftKey(sessionId, target)
    let restored: AgentDraft | null = null
    let original: string | null = null
    try { original = sessionStorage.getItem(key); restored = readAgentDraft(original) } catch { /* 浏览器禁用存储时仍可编辑。 */ }
    if (inspect) restored = null
    if (restored && (restored.kind !== base.kind || Boolean(restored.editRevision) !== Boolean(base.editRevision) || (base.editRevision && restored.name !== base.name))) restored = null
    const blocked = !inspect && original !== null && restored === null
    setRetainedDraft(blocked ? original : null)
    draftBase.current = base; draftBaseline.current = serializeAgentDraft(base); draftPaused.current = blocked
    setReadOnly(inspect); setDraftKey(inspect ? null : key); setDraftError(''); setDraftNotice(restored ? {key: 'ui.agent.author.draftRestored'} : '')
    applyDraft(restored ?? base); setError(''); setSaveUnconfirmed(false); setNotice(''); setOpen(true)
  }
  function discardDraft() {
    if (operation.current || !draftKey || !draftBase.current) return
    try { sessionStorage.removeItem(draftKey); setDraftError('') } catch { setDraftError({key: 'ui.agent.author.draftUnavailable'}); return }
    draftPaused.current = true
    applyDraft(draftBase.current); setDraftNotice(''); setError(''); setNotice({key: 'ui.agent.author.draftDeleted'}); setOpen(false)
  }
  useEffect(() => {
    if (!open || !draftKey || draftPaused.current) return
    let outcome: ReturnType<typeof persistAgentDraft>
    try { outcome = persistAgentDraft(sessionStorage, draftKey, serializedDraft, draftBaseline.current) }
    catch { outcome = 'unavailable' }
    setDraftError(outcome === 'invalid' ? {key: 'ui.agent.author.draftTooLarge'} : outcome === 'unavailable' ? {key: 'ui.agent.author.draftUnavailable'} : '')
  }, [open, draftKey, serializedDraft])
  function fill(agent: Agent, inspect = false) {
    setPreviewAgent(agent)
    openDraft({version: 5, skills: agent.skills ?? null, name: agent.name + (agent.edit_revision ? '' : '-custom'), generatedName: newCommandId(),
      displayName: agent.display_name || (typeof agent.metadata?.display_name === 'string' ? agent.metadata.display_name : ''),
      profession: agent.profession || (typeof agent.metadata?.profession === 'string' ? agent.metadata.profession : ''),
      description: agent.description, prompt: agent.system_prompt ?? '', model: agent.model ?? '',
      runner: agent.runner || (typeof agent.metadata?.runner === 'string' ? agent.metadata.runner : ''),
      personalityPrompt: typeof agent.metadata?.personality_prompt === 'string' ? agent.metadata.personality_prompt : null,
      metadataText: JSON.stringify(agent.metadata ?? {}, null, 2),
      members: JSON.stringify(agent.members ?? [], null, 2),
      kind: agent.agent_type, scope: agent.edit_scope ?? (sessionId ? 'project' : 'user'), useNow: false, editRevision: agent.edit_revision ?? null,
    }, (agent.edit_revision ? 'edit:' + (agent.edit_scope ?? (sessionId ? 'project' : 'user')) + ':' : 'copy:') + agent.name, inspect)
  }
  useEffect(() => { if (seed) fill(seed) }, [seed])
  useEffect(() => { if (viewSeed) fill(viewSeed, true) }, [viewSeed])
  useEffect(() => {
    if (!open || readOnly) return
    const frame = requestAnimationFrame(() => {
      const target = nameInput.current
      const active = document.activeElement
      const editing = active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement || active instanceof HTMLSelectElement || active instanceof HTMLElement && active.isContentEditable
      if (editing && target?.closest('[role="dialog"]')?.contains(active)) return
      target?.focus()
    })
    return () => cancelAnimationFrame(frame)
  }, [open, seed, readOnly])
  function closeDialog() {
    if (operation.current) return
    setOpen(false)
    if (readOnly) onCloseView?.()
  }
  function beginCreate(type: 'agent' | 'team' = initialKind) {
    if (operation.current) return
    setPreviewAgent(null)
    openDraft({version: 5, skills: [], name: '', displayName: '', profession: '', generatedName: newCommandId(), description: '', prompt: '',
      model: '', runner: '', personalityPrompt: null, metadataText: '{}', members: '[]',
      kind: type, scope: sessionId ? 'project' : 'user', useNow: false, editRevision: null}, 'new:' + type)
  }
  async function importFile(file?: File) {
    if (!file || operation.current) return
    operation.current = true
    const generation = draftGeneration.current
    setBusy(true); setError(''); setSaveUnconfirmed(false); setNotice('')
    try {
      if (file.size > 256000) throw new ResourceError({key: 'ui.agent.author.large'})
      const data = await pluginRequest<{ agent: Agent }>('/agents/preview', 'POST', { markdown: await file.text() })
      if (ownsDraft(generation)) fill(data.agent)
    } catch (failure) { if (ownsDraft(generation)) setError(failureText(failure)) }
    finally { operation.current = false; if (live.current) setBusy(false); if (input.current) input.current.value = '' }
  }
  async function save(startAfterCreate = false) {
    if (retainedDraft !== null || operation.current) return
    operation.current = true
    const generation = draftGeneration.current
    setBusy(true); setError(''); setSaveUnconfirmed(false); setNotice('')
    try {
      const savedName = name.trim() || (displayName.trim() ? generatedName : '')
      const parsedMetadata: unknown = JSON.parse(metadataText)
      if (!parsedMetadata || typeof parsedMetadata !== 'object' || Array.isArray(parsedMetadata)) throw new ResourceError({key: 'ui.agent.author.invalidMetadata'})
      const savedMetadata = { ...parsedMetadata } as Record<string, unknown>
      if (personalityPrompt !== null) savedMetadata.personality_prompt = personalityPrompt
      else delete savedMetadata.personality_prompt
      delete savedMetadata.display_name; delete savedMetadata.profession
      if (displayName.trim()) savedMetadata.display_name = displayName.trim()
      if (profession.trim()) savedMetadata.profession = profession.trim()
      const rows = kind === 'team' ? (JSON.parse(members) as Member[]).map(row => effectiveMemberRunner(row) ? {...row, skills: null} : row) : []
      const result = await pluginRequest<AgentCreatedResponse | AgentUpdatedResponse>(editRevision ? '/agents/' + encodeURIComponent(savedName) : '/agents/create', editRevision ? 'PATCH' : 'POST', { name: savedName, skills: kind === 'agent' && runner ? null : skills, description: description.trim(), system_prompt: prompt, model: kind === 'agent' && runner ? '' : model, runner: kind === 'agent' ? runner || null : null, metadata: savedMetadata, agent_type: kind, members: rows, scope, session_id: sessionId, ...(editRevision ? { expected_revision: editRevision } : {}) })
      if (result.agent.name !== savedName || result.agent.agent_type !== kind) throw new ApiError({ok: false, status: 200,
        code: 'response/identity-mismatch', message: t('ui.http.invalidResponse'), copy: {key: 'ui.http.invalidResponse'}})
      if (!ownsDraft(generation)) return
      if (sessionId && result.mount) useChatStore.getState().setAgentMount(sessionId, result.mount)
      const refreshFailed = Boolean(result.refresh_errors?.length)
      const refreshDetails = (result.refresh_errors ?? []).slice(0, 3).map(entry => {
        const detail = entry.message.trim() ? entry.message : entry.code
        return detail.slice(0, 400)
      }).filter(Boolean)
      const syncNotice: ResourceText = refreshFailed ? {template: {key: 'ui.agent.author.refreshDetails', params: {count: result.refresh_errors?.length ?? 0, details: refreshDetails.length ? refreshDetails.reduce<ResourceText>((first, second) => first ? {template: {key: 'ui.common.feedbackPair', params: {first, second}}} : second, '') : {key: 'ui.agent.author.refreshUnknown'}}}} : ''

      let receipt: ResourceText = {key: refreshFailed ? 'ui.agent.author.refreshFailed' : editRevision ? 'ui.agent.author.edited' : 'ui.agent.author.saved'}
      const notes: ResourceText[] = syncNotice ? [syncNotice] : []
      let followupFailed = refreshFailed
      // 配置写入已成功，后续挂载或目录读取失败不能恢复成“新建未保存”。
      draftPaused.current = true
      try { if (draftKey) sessionStorage.removeItem(draftKey) }
      catch { followupFailed = true; notes.push({key: 'ui.agent.author.savedDraftCleanupFailed'}) }
      setGeneratedName(newCommandId()); setName(savedName); setNotice(receipt); setOpen(false); setEditRevision(null)
      let mounted = false
      if (!editRevision && startAfterCreate && sessionId) {
        try { const result = await pluginRequest<{ mount: AgentMountState }>(kind === 'team' ? '/agent-selection' : '/agent-mount', 'POST', { session_id: sessionId, name: savedName }); useChatStore.getState().setAgentMount(sessionId, result.mount); mounted = true }
        catch (failure) { receipt = {key: 'ui.agent.author.mountFailed'}; followupFailed = true; notes.push({template: {key: 'ui.agent.author.mountFailureDetails', params: {reason: failureText(failure)}}}) }
      }
      if (!ownsDraft(generation)) return
      try { await refresh() }
      catch (failure) { followupFailed = true; notes.push({template: {key: 'ui.agent.author.savedRefreshFailed', params: {reason: failureText(failure)}}}) }
      if (!ownsDraft(generation)) return
      const details = joinFeedback(notes)
      setNotice(joinFeedback([receipt, ...notes]))
      useAppStore.getState().toast({ title: receipt, description: details || undefined, tone: followupFailed ? 'warn' : 'success' })
      if (mounted) { useAppStore.getState().setSettingsOpen(false); requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('textarea[aria-autocomplete="list"]')?.focus()) }
    } catch (failure) {
      if (!ownsDraft(generation)) return
      if (failure instanceof ApiError && isUnconfirmedWrite(failure.failure)) {
        const reason = failureText(failure)
        setSaveUnconfirmed(true)
        setError({template: {key: 'ui.agent.author.saveUnconfirmed', params: {reason}}})
        try { await refresh() }
        catch (refreshFailure) {
          if (ownsDraft(generation)) setError({template: {key: 'ui.agent.author.saveUnconfirmedReadFailed',
            params: {reason, refreshReason: failureText(refreshFailure)}}})
        }
      } else setError(failureText(failure))
    }
    finally { operation.current = false; if (live.current) setBusy(false) }
  }
  const parsedMembers = readTeamMembers(members)
  const memberRows: Member[] = parsedMembers ?? []
  const memberConfigValid = parsedMembers !== null
  const changeMembers = (next: Member[]) => {
    setMembers(JSON.stringify(next, null, 2))
    setMemberNameEdits(previous => Object.fromEntries(Object.entries(previous).filter(([name]) => next.some(row => row.name === name))))
  }
  const updateMember = (index: number, patch: Partial<Member>) => {
    changeMembers(updateTeamMember(memberRows, index, patch, row => !effectiveMemberRunner(row)))
  }
  const pendingMemberName = (row: Member) => Object.prototype.hasOwnProperty.call(memberNameEdits, row.name) ? memberNameEdits[row.name] : row.name
  const commitMemberName = (index: number) => {
    const row = memberRows[index]
    const next = pendingMemberName(row)
    if (next !== row.name && !memberRenameIssue(memberRows, index, next)) updateMember(index, {name: next})
  }
  const label = (key: string) => t('ui.agent.author.' + key)


  const identity = (row: Member) => agents.find(agent => agent.name === row.agent)
  const memberLabel = (row: Member) => row.display_name?.trim() || identity(row)?.display_name || row.agent || (row.name === 'coordinator' ? label('coordinatorDefault') : row.name)
  const avatar = (agent: Agent | undefined) => <AgentAvatar src={agent?.avatar ? '/api/v1/plugins' + agent.avatar + (sessionId ? '?session_id=' + encodeURIComponent(sessionId) : '') : null} />
  const addAgent = (agent: string) => {
    if (!memberConfigValid || memberRows.length >= 8 || memberRows.some(row => row.agent === agent)) return
    const selected = agents.find(row => row.name === agent)
    const rows = selected?.runner && !memberRows.length ? [{ name: 'coordinator', role: 'lead', prompt: t('ui.agents.leadPrompt') }] : memberRows
    setMembers(JSON.stringify([...rows, { name: defaultTeamMemberName(rows, agent), role: rows.some(row => row.role === 'lead') ? 'member' : 'lead', agent, ...(selected?.runner ? { runner: selected.runner } : {}) }], null, 2))
  }
  const modelChoice = (value: string, change: (value: string) => void, title: string) => <label className="block space-y-2 text-sm text-muted">{label('modelChoice')}<select className={field} aria-label={title} value={value} onChange={event => change(event.target.value)}><option value="">{label('inheritModel')}</option>{value && !models.some(item => item.id === value) && <option value={value}>{value}</option>}{models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
  const memberRuntime = (row: Member) => resolveMemberRuntime(row, identity(row), model)
  const effectiveMemberRunner = (row: Member) => memberRuntime(row).runner
  const memberIssue = memberConfigValid ? teamMemberIssue(memberRows, row => !effectiveMemberRunner(row)) : 'memberJsonInvalid'
  const advancedMember = (row: Member, index: number) => <details open={row.agent ? !identity(row) || identity(row)?.enabled === false || identity(row)?.agent_type === 'team' : !row.prompt?.trim()} className="border-t border-line pt-2 text-xs text-muted">
    <summary className="cursor-pointer">{label('memberSettings')}</summary>
    <div className="mt-3 space-y-3">
      <label className="block space-y-1">{label('memberDisplayName')}<input aria-label={label('memberDisplayName') + ' · ' + row.name} className={field} maxLength={120} value={row.display_name ?? ''} onChange={event => updateMember(index, { display_name: event.target.value })} /></label>
      <label className="block space-y-1">{label('memberName')}<input aria-label={label('memberName') + ' ' + index} data-member-identifier={row.name} className={field} maxLength={120} value={pendingMemberName(row)} aria-invalid={pendingMemberName(row) !== row.name && Boolean(memberRenameIssue(memberRows, index, pendingMemberName(row)))} onChange={event => { const value = event.target.value; setMemberNameEdits(previous => { const next = {...previous}; if (value === row.name) delete next[row.name]; else next[row.name] = value; return next }) }} onBlur={() => commitMemberName(index)} onKeyDown={event => { if (event.nativeEvent.isComposing) return; if (event.key === 'Enter') { event.preventDefault(); commitMemberName(index) } else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); setMemberNameEdits(previous => { const next = {...previous}; delete next[row.name]; return next }) } }} /></label>
      {pendingMemberName(row) !== row.name && <p role="status" className="text-xs text-warn">{label(memberRenameIssue(memberRows, index, pendingMemberName(row)) || 'confirmMemberName')}</p>}
      {row.role !== 'lead' && <fieldset className="space-y-2"><legend>{label('dependencies')}</legend>{memberRows.filter(candidate => candidate.name !== row.name && candidate.role !== 'lead').map(candidate => <label key={candidate.name} className="flex items-center gap-2"><input type="checkbox" aria-label={label('dependencies') + ' · ' + row.name + ' · ' + candidate.name} checked={row.depends_on?.includes(candidate.name) ?? false} onChange={event => updateMember(index, {depends_on: event.target.checked ? [...(row.depends_on ?? []), candidate.name] : (row.depends_on ?? []).filter(name => name !== candidate.name)})} />{memberLabel(candidate)}</label>)}</fieldset>}
      <label className="block space-y-1">{label('prompt')}<textarea aria-label={label('prompt') + ' ' + row.name} className={field} value={row.prompt ?? ''} onChange={event => updateMember(index, { prompt: event.target.value })} /></label>

    </div>
  </details>
  let configMetadata: Record<string, unknown> | null = null
  try { const value: unknown = JSON.parse(metadataText); if (value && typeof value === 'object' && !Array.isArray(value)) configMetadata = value as Record<string, unknown> } catch { /* 保留高级配置正在编辑的原文。 */ }
  const patchMetadata = (patch: Record<string, unknown>) => { if (!configMetadata) { setError({key: 'ui.agent.author.invalidMetadata'}); return } setMetadataText(JSON.stringify({ ...configMetadata, ...patch }, null, 2)) }
  const validationMessage = !(name.trim() || displayName.trim()) ? label(kind === 'team' ? 'teamNameRequired' : 'agentNameRequired')
    : !configMetadata ? label('invalidMetadata')
    : kind === 'team' && configMetadata.member_timeout_seconds !== undefined && (typeof configMetadata.member_timeout_seconds !== 'number' || !Number.isFinite(configMetadata.member_timeout_seconds) || configMetadata.member_timeout_seconds < 60 || configMetadata.member_timeout_seconds > 86400) ? label('timeoutInvalid')
    : kind === 'agent' ? ''
    : Object.keys(memberNameEdits).length ? label('confirmMemberName')
    : memberRows.length < 2 && memberConfigValid ? label('needMembers')
    : memberRows.length > 8 ? label('memberLimit')
    : memberIssue ? label(memberIssue)
    : ''
  const validationNotice = [validationMessage, kind === 'team' && memberConfigValid && memberRows.length < 2 ? label('needMembers') : ''].filter((message, index, messages) => message && messages.indexOf(message) === index).join(' ')
  const canSave = !busy && retainedDraft === null && !validationMessage
  const dispatchEditor = <section className="space-y-3 rounded-xl border border-line bg-canvas p-4">
    <h3 className="text-sm font-medium text-ink">{label('dispatchTitle')}</h3>
    <label className="block space-y-2 text-sm text-muted">{label('timeoutMinutes')}<input type="number" min={1} max={1440} step="any" disabled={!configMetadata} aria-label={label('timeoutMinutes')} className={field} placeholder={label('timeoutDefault')} value={typeof configMetadata?.member_timeout_seconds === 'number' ? configMetadata.member_timeout_seconds / 60 : ''} onChange={event => { if (event.target.value) patchMetadata({member_timeout_seconds: Number(event.target.value) * 60}); else if (configMetadata) { const next = {...configMetadata}; delete next.member_timeout_seconds; setMetadataText(JSON.stringify(next, null, 2)) } }} /></label>
    <p className="text-xs leading-relaxed text-faint">{label('timeoutHint')}</p>
    <label className="flex items-start gap-2 text-sm text-muted"><Switch checked={configMetadata?.member_context !== 'ephemeral'} disabled={!configMetadata} onChange={event => patchMetadata({ member_context: event.target.checked ? 'persistent' : 'ephemeral' })} />{label('persistentContext')}</label>
    <p className="text-xs leading-relaxed text-faint">{label('persistentContextHint')}</p>
    <label className="block space-y-2 text-sm text-muted">{label('dispatchStrategy')}<select className={field} aria-label={label('dispatchStrategy')} value={String(configMetadata?.acceptance ?? '').trim().toLowerCase() === 'strict' ? 'workflow' : configMetadata?.dispatch === 'workflow' ? 'workflow' : 'smart'} disabled={!configMetadata || String(configMetadata.acceptance ?? '').trim().toLowerCase() === 'strict'} onChange={event => patchMetadata({ dispatch: event.target.value })}><option value="smart">{label('dispatchSmart')}</option><option value="workflow">{label('dispatchWorkflow')}</option></select></label>
    <p className="text-xs leading-relaxed text-faint">{label('dispatchHint')}</p>
    <label className="flex items-start gap-2 text-sm text-muted"><Switch checked={String(configMetadata?.acceptance ?? '').trim().toLowerCase() === 'strict'} disabled={!configMetadata} onChange={event => patchMetadata({ acceptance: event.target.checked ? 'strict' : 'report' })} />{label('strictAcceptance')}</label>
    <p className="text-xs leading-relaxed text-faint">{label('strictAcceptanceHint')}</p>
  </section>
  const personalityEditor = <details className="rounded-xl border border-line bg-canvas p-4"><summary className="cursor-pointer text-sm font-medium text-ink">{label('personalityOptional')}</summary><div className="mt-4 space-y-3">
    <p className="text-xs leading-relaxed text-muted">{t(readOnly ? 'ui.personality.readOnlyHint' : 'ui.personality.copyHint')}</p>
    {profileError && <p role="alert" className="text-xs text-danger">{noticeText(profileError)}</p>}
    <details className="rounded-lg border border-line p-3"><summary className="cursor-pointer text-sm">{t('ui.personality.reference')}</summary><div className="mt-3 flex flex-wrap gap-1 rounded-lg bg-raised p-1">{(['all', 'builtin', 'user'] as const).map(value => <button key={value} type="button" aria-pressed={profileSource === value} onClick={() => setProfileSource(value)} className={`rounded-md px-3 py-1.5 text-xs ${profileSource === value ? 'bg-canvas font-medium text-ink shadow-sm' : 'text-muted hover:text-ink'}`}>{t('ui.personality.tab.' + value)}</button>)}</div><div className="mt-3 grid max-h-64 gap-2 overflow-y-auto sm:grid-cols-2">{profiles.filter(profile => profileSource === 'all' || profile.source === profileSource).map(profile => <button key={profile.id} type="button" className="rounded-lg border border-line p-3 text-left hover:border-brand focus-visible:outline-brand" onClick={() => { setPersonalityPrompt(profile.prompt) }}><span className="block text-sm font-medium">{profile.name}{profile.source === 'builtin' && <span className="ml-2 text-xs font-normal text-muted">{t('ui.personality.tab.builtin')}</span>}</span><span className="mt-1 block text-xs text-muted">{profile.description}</span></button>)}</div></details>
    <label className="block space-y-2 text-sm text-muted">{t('ui.personality.content')}<textarea className={field + ' min-h-48 resize-y leading-relaxed'} aria-label={t('ui.personality.content')} value={personalityPrompt ?? ''} maxLength={1000} onChange={event => setPersonalityPrompt(event.target.value)} /></label>
  </div></details>
  const advanced = <details className="rounded-xl border border-line bg-canvas p-4 text-sm text-muted">
    <summary className="cursor-pointer font-medium">{label('advanced')}</summary>
    <div className="mt-4 space-y-4">
      <label className="block space-y-1">{label(kind === 'team' ? 'teamIdentifier' : 'agentIdentifier')}<input disabled={Boolean(editRevision)} className={field} aria-label={label(kind === 'team' ? 'teamIdentifier' : 'agentIdentifier')} value={name} placeholder={generatedName} maxLength={64} onChange={event => setName(event.target.value)} /><span className="block text-xs text-faint">{label(kind === 'team' ? 'teamCommandHint' : 'commandHint')}</span></label>
      <label className="block space-y-2">{label('metadata')}<p className="text-xs text-faint">{label(kind === 'team' ? 'teamMetadataHint' : 'metadataHint')}</p><textarea className={field + ' min-h-32 font-mono'} aria-label={label('metadata')} value={metadataText} onChange={event => editMetadata(event.target.value)} /></label>
      {kind === 'team' && <>
        <details><summary className="cursor-pointer">{label('members')}</summary><textarea className={field + ' mt-2 min-h-40 font-mono'} aria-label={label('members')} value={members} onChange={event => { const value = event.target.value; setMembers(value); const rows = readTeamMembers(value); if (rows) setMemberNameEdits(previous => Object.fromEntries(Object.entries(previous).filter(([name]) => rows.some(row => row.name === name)))) }} /></details>
      </>}
    </div>
  </details>
  const scopeChoice = <label className="block space-y-2 text-sm font-medium text-ink">{t('ui.scope.label')}<select className={field} aria-label={t('ui.scope.label')} disabled={Boolean(editRevision)} value={scope} onChange={event => setScope(event.target.value as 'user' | 'project')}><option value="user">{t('ui.scope.user')}</option><option value="project" disabled={!sessionId}>{t('ui.scope.project')}</option></select></label>
  const instructionEditor = <label className="block space-y-2 text-sm font-medium text-ink">{label('prompt')}<textarea className={field + ' min-h-32 resize-y leading-relaxed'} aria-label={label('prompt')} placeholder={label('instructionPlaceholder')} value={prompt} maxLength={64000} onChange={event => setPrompt(event.target.value)} /></label>
  return <div className="space-y-3">
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" disabled={busy} onClick={() => beginCreate(initialKind)}>{label(initialKind === 'team' ? 'newTeam' : 'newAgent')}</Button>
      <details className="ml-auto text-xs text-muted"><summary className="cursor-pointer rounded-lg px-3 py-2">{label('importConfig')}</summary><div className="mt-2 space-y-3 rounded-xl border border-line bg-canvas p-3">
        <Button size="sm" variant="ghost" icon={<Upload className="h-4 w-4" />} disabled={busy} onClick={() => input.current?.click()}>{label('import')}</Button>
      </div></details>
      <input ref={input} type="file" accept=".md,text/markdown,text/plain" hidden onChange={event => void importFile(event.target.files?.[0])} />
    </div>
    {!open && error && <p role="alert" className={'text-sm ' + (saveUnconfirmed ? 'text-warn' : 'text-danger')}>{noticeText(error)}</p>}
    {!open && notice && <p role="status" className="text-sm text-brand">{noticeText(notice)}</p>}
    {open && <Overlay labelledBy={titleId} onEscape={event => { const target = event.target; if (!(target instanceof HTMLInputElement)) return false; const key = target.dataset.memberIdentifier; if (key === undefined || !Object.prototype.hasOwnProperty.call(memberNameEdits, key)) return false; setMemberNameEdits(previous => { const next = {...previous}; delete next[key]; return next }); return true }} onClose={closeDialog} className="flex h-authoring max-w-6xl flex-col p-0 sm:h-authoring-wide">
      <header className="flex shrink-0 items-start justify-between gap-4 border-b border-line px-5 py-5 sm:px-7">
        <div className="space-y-2"><h2 id={titleId} className="text-xl font-semibold tracking-tight text-ink">{readOnly ? t('ui.agent.author.details') : label(editRevision ? kind === 'team' ? 'editTeamTitle' : 'editAgentTitle' : kind === 'team' ? 'newTeam' : 'newAgent')}</h2><p className="max-w-prose text-sm leading-relaxed text-muted">{label(kind === 'team' ? 'teamLayoutHint' : 'agentLayoutHint')}</p>{!readOnly && <p role="status" className="text-xs leading-relaxed text-muted">{retainedDraft !== null ? label('draftUnsupported') : draftError ? label('draftNotRetained') : noticeText(draftNotice) || label('draftHint')}</p>}</div>
        <Button variant="ghost" size="sm" disabled={busy} aria-label={label('cancel')} onClick={closeDialog} icon={<X className="h-5 w-5" />} />
      </header>
      <fieldset disabled={busy || readOnly || retainedDraft !== null} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-5 py-5 disabled:opacity-90 sm:px-7">
        {kind === 'team' ? <div className="grid gap-5 md:grid-cols-2">
          <div className="md:col-span-2 space-y-4"><label className="block space-y-2 text-sm font-medium text-ink">{label('teamName')}<input ref={nameInput} className={field + ' text-base'} aria-label={label('teamName')} placeholder={label('teamNamePlaceholder')} value={displayName} maxLength={120} onChange={event => setDisplayName(event.target.value)} /></label></div>
          <TeamMemberPicker sessionId={sessionId} agents={agents} selected={memberRows.flatMap(row => row.agent ? [row.agent] : [])} count={memberRows.length} valid={memberConfigValid} onAdd={addAgent} onAddExternal={runner => changeMembers(addExternalTeamMember(memberRows, runner, {lead: t('ui.agents.leadPrompt'), member: t('ui.agents.memberPrompt'), leadName: label('coordinatorDefault')}))} />
          <section className="flex h-team-picker min-h-80 flex-col rounded-2xl border border-line bg-canvas" aria-label={label('selectedMembers')}>
            <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3"><h3 className="text-sm font-medium text-ink">{label('selectedMembers')} <span className="ml-2 font-normal text-faint">{memberRows.length} / 8</span></h3><Button size="sm" variant="secondary" disabled={!memberConfigValid || memberRows.length >= 8} onClick={() => { let suffix = 1; while (memberRows.some(row => row.name === 'custom-' + suffix)) suffix++; const lead = !memberRows.some(row => row.role === 'lead'); changeMembers([...memberRows, {name: 'custom-' + suffix, display_name: label(lead ? 'customLeadName' : 'customMemberName'), role: lead ? 'lead' : 'member', execution: 'model', prompt: t(lead ? 'ui.agents.leadPrompt' : 'ui.agents.memberPrompt')}]) }}>{label(memberRows.some(row => row.role === 'lead') ? 'addCustomMember' : memberRows.length ? 'addLead' : 'addCustomMember')}</Button></div>
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain p-4">
              {memberRows.length === 0 && <div className="flex min-h-44 flex-col items-center justify-center gap-3 text-center text-muted"><Users className="h-8 w-8 text-faint" /><p className="text-sm">{label('selectionEmpty')}</p><p className="max-w-xs text-xs leading-relaxed text-faint">{label('leaderHelp')}</p></div>}
              {memberRows.map((row, index) => <article key={index} className={'space-y-3 rounded-xl border bg-surface p-3 ' + (row.role === 'lead' ? 'border-brand/35' : 'border-line')}>
                <div className="flex items-center gap-3">{avatar(identity(row))}<div className="min-w-0 flex-1"><p className="truncate text-sm font-medium text-ink">{memberLabel(row)}</p><p className="mt-1 line-clamp-1 text-xs text-muted">{identity(row)?.profession || identity(row)?.description || row.prompt}</p></div>
                  <button type="button" disabled={Boolean(effectiveMemberRunner(row))} aria-label={label('setLeader') + ' · ' + memberLabel(row)} aria-pressed={row.role === 'lead'} title={effectiveMemberRunner(row) ? label('externalLead') : undefined} onClick={() => updateMember(index, { role: 'lead' })} className={'inline-flex min-h-9 shrink-0 items-center gap-1 rounded-lg px-2 text-xs font-medium transition-colors disabled:opacity-40 ' + (row.role === 'lead' ? 'bg-brand text-brand-ink' : 'bg-raised text-muted hover:text-brand')}><Crown className="h-3.5 w-3.5" />{label(row.role === 'lead' ? 'leaderBadge' : 'setLeader')}</button>
                  <button type="button" aria-label={label('remove') + ' · ' + memberLabel(row)} onClick={() => changeMembers(removeTeamMember(memberRows, index, row => !effectiveMemberRunner(row)))} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-faint hover:bg-raised hover:text-danger"><X className="h-4 w-4" /></button>
                </div>{row.depends_on?.length ? <p className="text-xs leading-relaxed text-muted">{t('ui.team.member.after', { members: row.depends_on.map(name => { const dependency = memberRows.find(member => member.name === name); return dependency ? memberLabel(dependency) : name }).join(', ') })}</p> : null}{effectiveMemberRunner(row) && <p className="text-xs leading-relaxed text-muted">{label('externalLeadHelp')}</p>}{advancedMember(row, index)}
              </article>)}
            </div>
          </section>
          <div className="md:col-span-2 space-y-4"><label className="block space-y-2 text-sm font-medium text-ink">{label('teamDescription')}<textarea className={field + ' min-h-24 resize-y leading-relaxed'} aria-label={label('teamDescription')} placeholder={label('teamDescriptionHint')} value={description} maxLength={1000} onChange={event => setDescription(event.target.value)} /></label>{instructionEditor}{scopeChoice}<details className="rounded-xl border border-line p-4"><summary className="cursor-pointer text-sm font-medium">{label('executionSettings')}</summary><div className="mt-4">{dispatchEditor}</div></details>{advanced}</div>
        </div> : <div className="grid gap-6 md:grid-cols-authoring">
          <div className="space-y-5"><label className="block space-y-2 text-sm font-medium text-ink">{label('friendlyName')}<input ref={nameInput} className={field + ' text-base'} aria-label={label('friendlyName')} placeholder={label('agentNamePlaceholder')} value={displayName} maxLength={120} onChange={event => setDisplayName(event.target.value)} /></label>
            <label className="block space-y-2 text-sm text-muted">{label('profession')}<input className={field} aria-label={label('profession')} value={profession} maxLength={120} onChange={event => setProfession(event.target.value)} /></label>
            <label className="block space-y-2 text-sm font-medium text-ink">{label('descriptionOptional')}<textarea className={field + ' min-h-36 resize-y leading-relaxed'} aria-label={label('friendlyDescription')} placeholder={label('descriptionPlaceholder')} value={description} maxLength={1000} onChange={event => setDescription(event.target.value)} /></label>
            {instructionEditor}
            <AgentSkillPicker sessionId={sessionId} value={skills} external={Boolean(runner)} onChange={setSkills} />
            {scopeChoice}
            <details className="rounded-xl border border-line bg-canvas p-4"><summary className="cursor-pointer text-sm font-medium text-ink">{label('executionSettings')}</summary><div className="mt-4 space-y-4"><label className="block space-y-2 text-sm text-muted">{label('executionMode')}<select className={field} aria-label={label('executionMode')} value={runner} onChange={event => { setRunner(event.target.value); if (event.target.value) setModel('') }}><option value="">{label('modelRuntime')}</option><option value="codex">Codex</option><option value="claude">Claude Code</option><option value="opencode">OpenCode</option></select></label>{!runner ? modelChoice(model, setModel, label('modelChoice')) : <div className="space-y-3"><p className="text-xs leading-relaxed text-faint">{label('externalModelHint')}</p>{runnerStatus(runner)}</div>}</div></details>
            {personalityEditor}{advanced}
          </div>
          <aside className="h-fit rounded-2xl border border-line bg-raised/40 p-5 text-center"><p className="text-xs text-faint">{label('preview')}</p><div className="my-5 flex justify-center">{avatar(previewAgent ?? undefined)}</div><h3 className="text-xl font-semibold text-ink">{displayName.trim() || label('previewName')}</h3><p className="mt-2 text-sm text-muted">{profession || label('previewProfession')}</p><p className="mt-5 whitespace-pre-wrap text-sm leading-relaxed text-muted [overflow-wrap:anywhere]">{description || prompt || label('previewDescription')}</p></aside>
        </div>}
      </fieldset>
      {readOnly ? <footer className="flex shrink-0 justify-end gap-2 border-t border-line bg-surface px-5 py-4 sm:px-7">
        <Button size="sm" variant="secondary" onClick={closeDialog}>{t('ui.agent.author.close')}</Button>
        <Button size="sm" variant="primary" onClick={() => { if (!viewSeed) return; onCloseView?.(); fill({ ...viewSeed, edit_revision: undefined }) }}>{label('copy')}</Button>
      </footer> : <footer className="shrink-0 space-y-3 border-t border-line bg-surface px-5 py-4 sm:px-7">
        {validationNotice && <p role="status" className="text-xs leading-relaxed text-muted [overflow-wrap:anywhere]">{validationNotice}</p>}
        {retainedDraft !== null && <div role="alert" className="space-y-2 text-xs text-danger"><p>{label('draftUnsupported')}</p><div className="flex flex-wrap gap-2"><Button size="sm" variant="secondary" onClick={() => download('agent-draft-original.json', retainedDraft, 'application/json')}>{label('downloadOriginalDraft')}</Button><Button size="sm" variant="ghost" onClick={discardDraft}>{label('discardDraft')}</Button></div></div>}
        {draftError && <p role="alert" className="text-xs text-danger">{noticeText(draftError)}</p>}
        {error && <p role="alert" className={'text-sm [overflow-wrap:anywhere] ' + (saveUnconfirmed ? 'text-warn' : 'text-danger')}>{noticeText(error)}</p>}
        <div className="flex flex-wrap items-center justify-end gap-3">
          <div className="flex flex-wrap gap-2"><details className="text-sm"><summary className="cursor-pointer rounded-lg px-3 py-2 text-muted">{t('ui.personality.moreActions')}</summary><div className="mt-2 flex flex-wrap gap-2"><Button size="sm" variant="ghost" disabled={busy} onClick={discardDraft}>{label('discardDraft')}</Button>{editRevision && <Button size="sm" variant="ghost" disabled={busy || retainedDraft !== null} onClick={() => { if (operation.current) return; const copied = {...currentDraft, editRevision: null, name: name.slice(0, 57) + '-custom'}; draftBase.current = copied; draftBaseline.current = ''; setDraftKey(agentDraftKey(sessionId, 'new:' + kind)); applyDraft(copied); setDraftNotice(''); setError('') }}>{label('editCopy')}</Button>}</div></details><Button size="sm" loading={busy} disabled={!canSave} onClick={() => void save(false)}>{label(editRevision ? 'editSave' : 'createOnly')}</Button>{!editRevision && sessionId && <Button size="sm" variant="primary" loading={busy} disabled={!canSave} onClick={() => void save(true)}>{label('createAndChat')}</Button>}</div>
        </div>
      </footer>}
    </Overlay>}
  </div>
}
