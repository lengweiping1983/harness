import {useEffect, useRef, useState} from 'react'
import {t, useLocale} from '@/i18n'
import {usePageVisible} from '@/hooks/usePageVisible'
import {Button} from '@/components/ui/primitives'
import {failureText, ResourceError} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import type {ResourceText} from '@/types'
import * as sg from '@/lib/superagent'

const tabs = {missions:'ui.sg.missions',sources:'ui.sg.sources',channels:'ui.sg.channels',effects:'ui.sg.effects',attentions:'ui.sg.attentions',
  policy:'ui.sg.policy',devices:'ui.sg.devices',leases:'ui.sg.leases',mandates:'ui.sg.mandates',audit:'ui.sg.audit',events:'ui.sg.inputs',evaluations:'ui.sg.evaluations'}
const detailTabs = {jobs:'ui.sg.jobs',assignments:'ui.sg.assignments',artifacts:'ui.sg.artifacts',reviews:'ui.sg.reviews',inputs:'ui.sg.inputs',outbox:'ui.sg.outbox',leases:'ui.sg.leases'}
type Tab = keyof typeof tabs
type DetailTab = keyof typeof detailTabs
const value = (row: sg.Row, key: string) => typeof row[key] === 'string' || typeof row[key] === 'number' ? String(row[key]) : ''
const stateText = (state: unknown) => typeof state === 'string' && Object.hasOwn(sg.STATE_LABELS,state) ? t(sg.STATE_LABELS[state]) : String(state ?? '')
function parsed(raw: string): sg.Row {
  try {const result: unknown = JSON.parse(raw); if (sg.isRow(result)) return result} catch { /* 原文保留在编辑框，错误由资源呈现。 */ }
  throw new ResourceError({key:'ui.sg.error.invalid'})
}

export function SuperagentPanel({active = true}: {active?: boolean}) {
  useLocale()
  const visible = usePageVisible() && active
  const state = sg.useWorkbenchStore(item => item)
  const current = state.connections.find(item => item.connection_id === state.selected)
  const [tab,setTab] = useState<Tab>('missions')
  const [missionId,setMissionId] = useState('')
  const [detailTab,setDetailTab] = useState<DetailTab>('jobs')
  const [rows,setRows] = useState<sg.Page | null>(null)
  const [detail,setDetail] = useState<sg.MissionDetail | null>(null)
  const [error,setError] = useState<ResourceText>('')
  const [loading,setLoading] = useState(false)
  const [options,setOptions] = useState<sg.Row[]>([])
  const draft = state.drafts[state.selected] ?? {objective:'',source:'',actor:'',thread:''}
  const objective = draft.objective, localSource = draft.source, actor = draft.actor, thread = draft.thread
  const updateDraft = (changes: Partial<typeof draft>) => sg.useWorkbenchStore.setState(current => ({drafts:{...current.drafts,[state.selected]:{...draft,...changes}}}))
  const [editor,setEditor] = useState<{source: sg.Row; text: string; enabled: boolean} | null>(null)
  const [policy,setPolicy] = useState<{revision: number; text: string} | null>(null)
  const [target,setTarget] = useState<sg.Row | null>(null)
  const [instruction,setInstruction] = useState('')
  const [effect,setEffect] = useState<sg.Row | null>(null)
  const [note,setNote] = useState('')
  const [attention,setAttention] = useState<sg.Row | null>(null)
  const [answer,setAnswer] = useState('')
  const [media,setMedia] = useState<Record<string,sg.Row>>({})
  const controller = useRef<AbortController>()
  const generation = useRef(0)
  const catalogGeneration = useRef(0)
  const viewEpoch = useRef(0)
  const mounted = useRef(true)
  const scope = `${current?.connection_id ?? ''}\0${missionId}\0${tab}\0${detailTab}`
  const scopeRef = useRef(scope); scopeRef.current = scope
  const visibleRef = useRef(visible); visibleRef.current = visible
  const mutation = current ? state.writes[current.connection_id] : undefined
  const busy = !!mutation?.busy || !!mutation?.unconfirmed
  const activeSource = options.find(item => item.source_id === localSource) ?? (options.length === 1 ? options[0] : undefined)
  const actors = activeSource && Array.isArray(activeSource.actors) ? activeSource.actors as string[] : []
  const threads = activeSource && Array.isArray(activeSource.threads) ? activeSource.threads as string[] : []
  const selectedActor = actors.includes(actor) ? actor : actors.length === 1 ? actors[0] : ''
  const selectedThread = threads.includes(thread) ? thread : threads.length === 1 ? threads[0] : ''

  const invalidate = () => {generation.current++; controller.current?.abort()}
  const load = async (more = false) => {
    if (!current || !visibleRef.current || mutation?.busy || mutation?.unconfirmed) return
    invalidate()
    const version = generation.current, owner = scopeRef.current
    const abort = new AbortController(); controller.current = abort
    setLoading(true); setError('')
    try {
      const [connection,locals] = await Promise.all([sg.connectionState(current,abort.signal),sg.read(current,'local_options',{},abort.signal)])
      if (!Array.isArray(locals.sources) || locals.sources.some(item => !sg.isRow(item) || typeof item.source_id !== 'string'
        || !Array.isArray(item.actors) || !Array.isArray(item.threads) || [...item.actors,...item.threads].some(value => typeof value !== 'string'))) {
        throw new ResourceError({key:'ui.sg.error.protocol'})
      }
      if (!mounted.current || !visibleRef.current || abort.signal.aborted || generation.current !== version || owner !== scopeRef.current) return
      sg.useWorkbenchStore.setState(state => ({connections:state.connections.map(item => item.connection_id === connection.connection_id ? connection : item)}))
      setOptions(locals.sources as sg.Row[])
      let next: sg.Page | null = null, nextDetail: sg.MissionDetail | null = null, nextPolicy: {revision: number; text: string} | null = null
      if (missionId) {
        nextDetail = await sg.mission(current,missionId,abort.signal)
        next = await sg.page(current,detailTab,missionId,more ? rows?.next_cursor ?? undefined : undefined,abort.signal)
      } else if (tab === 'policy') {
        const result = await sg.read(current,'policy',{},abort.signal)
        if (!sg.isRow(result.policy) || !Number.isSafeInteger(result.policy.revision) || !sg.isRow(result.policy.policy)) throw new ResourceError({key:'ui.sg.error.protocol'})
        nextPolicy = {revision:Number(result.policy.revision),text:JSON.stringify(result.policy.policy,null,2)}
      } else next = await sg.page(current,tab,undefined,more ? rows?.next_cursor ?? undefined : undefined,abort.signal)
      if (!mounted.current || !visibleRef.current || abort.signal.aborted || generation.current !== version || owner !== scopeRef.current) return
      if (next) setRows(more && rows ? sg.appendPage(rows,next) : next)
      else setRows(null)
      setDetail(nextDetail); if (nextPolicy) setPolicy(nextPolicy)
    } catch (failure) {
      if (mounted.current && visibleRef.current && !abort.signal.aborted && version === generation.current && owner === scopeRef.current) setError(failureText(failure))
    } finally {
      if (mounted.current && version === generation.current && owner === scopeRef.current) setLoading(false)
    }
  }

  useEffect(() => {
    mounted.current = true
    return () => {mounted.current = false; invalidate()}
  },[])
  useEffect(() => {
    if (!visible) {invalidate(); setLoading(false); return}
    const version = ++catalogGeneration.current
    const abort = new AbortController()
    void sg.listConnections(abort.signal).then(connections => {
      if (mounted.current && !abort.signal.aborted && catalogGeneration.current === version) {
        sg.useWorkbenchStore.setState(state => ({connections,selected:connections.some(item => item.connection_id === state.selected) ? state.selected : state.selected ? '' : connections[0]?.connection_id ?? ''}))
      }
    }).catch(failure => {if (mounted.current && !abort.signal.aborted && catalogGeneration.current === version) setError(failureText(failure))})
    return () => {abort.abort()}
  },[visible])
  useEffect(() => {viewEpoch.current++},[scope,visible])
  useEffect(() => {
    invalidate(); setRows(null); setDetail(null); setEditor(null); setPolicy(null); setTarget(null); setEffect(null); setAttention(null); setMedia({}); setError('')
    setOptions([])
    return invalidate
  },[scope])
  useEffect(() => {
    if (current && visible && !mutation?.busy && !mutation?.unconfirmed) void load()
    return invalidate
  },[scope,visible,mutation?.busy,mutation?.unconfirmed])

  const connect = async () => {
    const catalogVersion = ++catalogGeneration.current
    const epoch = viewEpoch.current
    const original = scopeRef.current
    const path = state.configPath
    setLoading(true); setError('')
    try {
      const connection = await sg.connect(path)
      // 已受理连接保留在共享界面状态，关闭页面后也不会转交到另一实例。
      sg.useWorkbenchStore.setState(state => ({connections:[...state.connections.filter(item => item.connection_id !== connection.connection_id),connection]}))
      if (mounted.current && visibleRef.current && scopeRef.current === original && viewEpoch.current === epoch && catalogGeneration.current === catalogVersion && sg.useWorkbenchStore.getState().configPath === path) {
        setMissionId(''); sg.useWorkbenchStore.setState({selected:connection.connection_id})
      }
    } catch (failure) {if (mounted.current && viewEpoch.current === epoch && scopeRef.current === original) setError(failureText(failure))}
    finally {if (mounted.current && viewEpoch.current === epoch && catalogGeneration.current === catalogVersion && scopeRef.current === original) setLoading(false)}
  }
  const execute = async (action: Parameters<typeof sg.write>[1], payload: sg.Row) => {
    if (!current || busy || sg.useWorkbenchStore.getState().writes[current.connection_id]?.busy || sg.useWorkbenchStore.getState().writes[current.connection_id]?.unconfirmed) return
    catalogGeneration.current++
    const epoch = viewEpoch.current
    const original = scopeRef.current
    invalidate(); setLoading(false); setError('')
    try {
      const result = await sg.write(current,action,payload)
      if (mounted.current && visibleRef.current && original === scopeRef.current && viewEpoch.current === epoch && action === 'media') {
        const mediaId = result.receipt.media_id
        if (typeof mediaId !== 'string') throw new ResourceError({key:'ui.sg.error.protocol'})
        sg.mediaUrl(current,mediaId)
        setMedia(previous => ({...previous,[String(payload.trigger_id)+'\0'+String(payload.resource_key)]:result.receipt}))
      }
    } catch (failure) {if (mounted.current && viewEpoch.current === epoch && original === scopeRef.current) setError(failureText(failure))}
  }
  const check = async () => {
    if (!current) return
    const original = scopeRef.current, epoch = viewEpoch.current
    try {await sg.checkRequest(current)} catch (failure) {if (mounted.current && viewEpoch.current === epoch && original === scopeRef.current) setError(failureText(failure))}
  }
  const inspectEffect = async (row: sg.Row) => {
    if (!current || busy) return
    invalidate()
    const version = generation.current, original = scopeRef.current
    const abort = new AbortController(); controller.current = abort
    setError('')
    try {
      const result = await sg.read(current,'effect',{identifier:String(row.effect_id)},abort.signal)
      if (!sg.isRow(result.effect) || result.effect.effect_id !== row.effect_id || result.effect.generation !== row.generation) throw new ResourceError({key:'ui.sg.error.protocol'})
      if (mounted.current && visibleRef.current && !abort.signal.aborted && original === scopeRef.current && version === generation.current) {
        setEffect(result.effect); setNote('')
      }
    } catch (failure) {if (mounted.current && !abort.signal.aborted && original === scopeRef.current && version === generation.current) setError(failureText(failure))}
  }
  const save = () => {
    try {
      if (editor) void execute('source',{source_id:editor.source.source_instance_id,expected_revision:editor.source.config_revision,config:parsed(editor.text),enabled:editor.enabled})
      else if (policy) void execute('policy',{expected_revision:policy.revision,policy:parsed(policy.text)})
    } catch (failure) {setError(failureText(failure))}
  }
  const select = (key: string) => {invalidate(); setMissionId(''); sg.useWorkbenchStore.setState({selected:key})}
  const enterMission = (id: string) => {invalidate(); setMissionId(id); setDetailTab('jobs')}
  const button = 'rounded-lg border border-line px-3 py-2 text-sm hover:bg-raised disabled:opacity-50'
  const field = 'w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm'
  return <section aria-label={t('ui.sg.title')} className="space-y-4">
    <div><h2 className="text-lg font-semibold">{t('ui.sg.title')}</h2><p className="mt-1 text-sm text-muted">{t('ui.sg.emptyConnection')}</p></div>
    <div className="flex flex-wrap items-end gap-2">
      <label className="min-w-48 flex-1 text-sm">{t('ui.sg.config')}<input className={field} value={state.configPath} onChange={event => sg.useWorkbenchStore.setState({configPath:event.target.value})} /></label>
      <Button disabled={loading || !state.configPath.trim()} onClick={() => void connect()}>{t('ui.sg.connect')}</Button>
    </div>
    {state.connections.length ? <div className="flex flex-wrap items-center gap-2">
      <label className="text-sm">{t('ui.sg.instance')}<select className={field} value={state.selected} onChange={event => select(event.target.value)}>
        {state.connections.map(item => <option key={item.connection_id} value={item.connection_id}>{item.instance_id}</option>)}
      </select></label>
      {current ? <><span className="text-sm text-muted">{t('ui.sg.runtime')}: {stateText(current.runtime)}</span>
        <button type="button" className={button} disabled={busy || current.runtime === 'running'} onClick={() => void execute('start',{})}>{t('ui.sg.start')}</button>
        <button type="button" className={button} disabled={busy || current.runtime !== 'running'} onClick={() => void execute('stop',{})}>{t('ui.sg.stop')}</button>
        <button type="button" className={button} disabled={busy || loading} onClick={() => void load()}>{t('ui.sg.refresh')}</button></> : null}
    </div> : null}
    {error || mutation?.error ? <p role="alert" className="text-sm text-danger">{noticeText(error || mutation?.error || '')}</p> : null}
    {mutation ? <details className="text-sm"><summary>{t('ui.sg.receipt')}</summary>
      <p className="break-all text-xs text-muted">{t('ui.sg.request')}: {mutation.requestId}</p>
      <p>{t('ui.sg.accepted')}</p>
      {mutation.receipt ? <pre className="max-h-48 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(mutation.receipt,null,2)}</pre> : null}
      <button type="button" className={button} disabled={mutation.busy} onClick={() => void check()}>{t('ui.sg.checkRequest')}</button>
    </details> : null}
    {current ? <>
      {!missionId ? <nav className="flex flex-wrap gap-1" aria-label={t('ui.sg.title')}>{Object.entries(tabs).map(([key,label]) =>
        <button key={key} type="button" aria-pressed={tab === key} className={button} onClick={() => {invalidate(); setTab(key as Tab)}}>{t(label)}</button>)}</nav>
      : <div className="space-y-2"><button type="button" className={button} onClick={() => {invalidate(); setMissionId('')}}>{t('ui.sg.back')}</button>
        {detail ? <><h3 className="font-semibold">{value(detail.mission,'title')}</h3><p className="text-sm">{stateText(detail.mission.state)}</p>
          <p className="text-xs text-muted">{t('ui.sg.baseline')}: {value(detail.mission,'base_commit')}</p>
          {detail.plan ? <details><summary className="text-sm">{t('ui.sg.acceptance')}</summary><pre className="overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(detail.plan.acceptance_json,null,2)}</pre></details> : null}
          <div className="flex flex-wrap gap-2">{([{id:'pause',label:'ui.sg.pause'},{id:'resume',label:'ui.sg.resume'},{id:'cancel',label:'ui.sg.cancel'}] as const).map(action => <button key={action.id} type="button" className={button}
            disabled={busy || ['completed','cancelled','rejected','suppressed'].includes(String(detail.mission.state))}
            onClick={() => void execute('control',{action:action.id,mission_id:missionId})}>{t(action.label)}</button>)}</div></> : null}
        <nav className="flex flex-wrap gap-1" aria-label={t('ui.sg.missions')}>{Object.entries(detailTabs).map(([key,label]) =>
          <button key={key} type="button" className={button} aria-pressed={detailTab === key} onClick={() => {invalidate(); setDetailTab(key as DetailTab)}}>{t(label)}</button>)}</nav>
      </div>}
      {!missionId && tab === 'missions' ? <div className="space-y-2">
        <label className="block text-sm">{t('ui.sg.objective')}<textarea className={field} value={objective} onChange={event => updateDraft({objective:event.target.value})} /></label>
        <div className="flex flex-wrap gap-2">
          <label className="text-sm">{t('ui.sg.source')}<select className={field} value={(activeSource?.source_id as string) ?? ''} onChange={event => updateDraft({source:event.target.value,actor:'',thread:''})}>
            <option value="">{t('ui.sg.choose')}</option>{options.map(source => <option key={String(source.source_id)} value={String(source.source_id)}>{String(source.source_id)}</option>)}</select></label>
          <label className="text-sm">{t('ui.sg.actor')}<select className={field} value={selectedActor} onChange={event => updateDraft({actor:event.target.value})}><option value="">{t('ui.sg.choose')}</option>{actors.map(item => <option key={item}>{item}</option>)}</select></label>
          <label className="text-sm">{t('ui.sg.thread')}<select className={field} value={selectedThread} onChange={event => updateDraft({thread:event.target.value})}><option value="">{t('ui.sg.choose')}</option>{threads.map(item => <option key={item}>{item}</option>)}</select></label>
        </div>
        <button type="button" className={button} disabled={busy || !objective.trim() || !activeSource || !selectedActor || !selectedThread}
          onClick={() => void execute('submit',{text:objective,source_id:activeSource?.source_id,actor_id:selectedActor,thread_id:selectedThread})}>{t('ui.sg.submit')}</button>
      </div> : null}
      {policy ? <div className="space-y-2"><label className="block text-sm">{t('ui.sg.policyJson')}
        <textarea className={field+' min-h-64 font-mono'} disabled={busy} value={policy.text} onChange={event => setPolicy({...policy,text:event.target.value})} /></label>
        <button type="button" className={button} disabled={busy} onClick={save}>{t('ui.sg.save')}</button></div> : null}
      {rows ? <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr><th className="py-2">{t('ui.sg.reference')}</th><th>{t('ui.sg.state')}</th><th>{t('ui.sg.kind')}</th><th className="sr-only">{t('ui.sg.actions')}</th></tr></thead>
        <tbody>{rows.items.map(row => <tr key={sg.recordId(rows.resource,row)} className="border-t border-line">
          <td className="max-w-80 break-words py-3 pr-3">{String(row.title ?? row.node_id ?? row.question_text ?? row.file_name ?? row.source_instance_id ?? row.event_kind ?? row.kind ?? row.run_id ?? sg.recordId(rows.resource,row))}</td>
          <td className="pr-3">{stateText(row.state ?? (typeof row.enabled === 'number' ? row.enabled ? 'running' : 'stopped' : ''))}</td><td className="pr-3">{String(row.role ?? row.kind ?? row.event_kind ?? '')}</td>
          <td className="py-2">
            {!missionId && tab === 'missions' ? <button type="button" className={button} onClick={() => enterMission(String(row.mission_id))}>{t('ui.sg.details')}</button> : null}
            {!missionId && tab === 'sources' ? <button type="button" className={button} disabled={busy} onClick={() => setEditor({source:row,text:JSON.stringify(parsed(String(row.config_json)),null,2),enabled:!!row.enabled})}>{t('ui.sg.edit')}</button> : null}
            {(!missionId && ['audit','mandates','devices','leases'].includes(tab) || missionId && detailTab === 'leases') ? <details><summary>{t('ui.sg.details')}</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(row,null,2)}</pre></details> : null}
            {!missionId && tab === 'channels' ? <><details><summary>{t('ui.sg.replyTarget')}</summary><pre className="max-h-32 overflow-auto whitespace-pre-wrap text-xs">{String(row.address_json)}</pre></details><button type="button" className={button} disabled={busy} onClick={() => void execute('reply',{channel_id:row.channel_id,expected_source_revision:row.source_revision,enabled:!row.reply_enabled})}>{t(row.reply_enabled ? 'ui.sg.revokeReply' : 'ui.sg.grantReply')}</button></> : null}
            {!missionId && tab === 'attentions' ? <><p className="text-xs text-muted">{t('ui.sg.answerScope')}</p><pre className="whitespace-pre-wrap text-xs">{String(row.options_json ?? '')}</pre>{row.state === 'open' ? <button type="button" className={button} disabled={busy} onClick={() => {setAttention(row);setAnswer('')}}>{t('ui.sg.answer')}</button> : null}</> : null}
            {!missionId && tab === 'effects' ? <button type="button" className={button} disabled={busy} onClick={() => void inspectEffect(row)}>{t('ui.sg.verify')}</button> : null}
            {!missionId && tab === 'evaluations' && row.state === 'unavailable' ? <button type="button" className={button} disabled={busy} onClick={() => void execute('requeue',{evaluation_id:row.evaluation_id})}>{t('ui.sg.requeue')}</button> : null}
            {missionId && detailTab === 'jobs' && row.state === 'running' && ['work','integrate'].includes(String(row.role)) ? <button type="button" className={button} disabled={busy} onClick={() => {setTarget(row);setInstruction('')}}>{t('ui.sg.steer')}</button> : null}
            {(missionId && detailTab === 'inputs' || !missionId && tab === 'events') && typeof row.body_json === 'string' ? <details><summary>{t('ui.sg.inputs')}</summary>
              <p className="whitespace-pre-wrap">{String(parsed(row.body_json).text ?? '')}</p>
              {Array.isArray(parsed(row.body_json).attachments) ? (parsed(row.body_json).attachments as sg.Row[]).map(resource => {
                const key = String(row.trigger_id)+'\0'+String(resource.resource_key), cached = media[key]
                return <div key={key} className="space-y-2 py-2"><p>{String(resource.name || resource.type)}</p>
                  {cached ? <><a className="text-brand underline" href={sg.mediaUrl(current,String(cached.media_id))} target="_blank" rel="noreferrer">{t('ui.sg.download')}</a>
                    {visible && resource.type === 'image' ? <img alt={String(resource.name || t('ui.sg.media'))} className="max-h-64 max-w-full rounded-lg" src={sg.mediaUrl(current,String(cached.media_id))} /> : null}</>
                    : <button type="button" className={button} disabled={busy} onClick={() => void execute('media',{trigger_id:row.trigger_id,resource_key:resource.resource_key})}>{t('ui.sg.fetchMedia')}</button>}
                </div>
              }) : null}</details> : null}
          </td></tr>)}</tbody></table>{!rows.items.length ? <p className="py-3 text-sm text-muted">{t('ui.sg.empty')}</p> : null}
        {rows.has_more ? <button type="button" className={button} disabled={loading || busy} onClick={() => void load(true)}>{t('ui.sg.more')}</button> : null}
      </div> : null}
      {editor ? <div className="space-y-2 rounded-xl border border-line p-3"><h3 className="font-medium">{String(editor.source.source_instance_id)}</h3>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={editor.enabled} onChange={event => setEditor({...editor,enabled:event.target.checked})} />{t('ui.sg.enabled')}</label>
        <label className="block text-sm">{t('ui.sg.configJson')}<textarea className={field+' min-h-40 font-mono'} value={editor.text} onChange={event => setEditor({...editor,text:event.target.value})} /></label>
        <button type="button" className={button} disabled={busy} onClick={save}>{t('ui.sg.save')}</button></div> : null}
      {target ? <div className="space-y-2"><label className="block text-sm">{t('ui.sg.instruction')}<textarea className={field} value={instruction} onChange={event => setInstruction(event.target.value)} /></label>
        <button type="button" className={button} disabled={busy || !instruction.trim()} onClick={() => void execute('steer',{text:instruction,job_id:target.job_id,
          ...(activeSource && selectedActor && selectedThread ? {source_id:activeSource.source_id,actor_id:selectedActor,thread_id:selectedThread} : {})})}>{t('ui.sg.steer')}</button></div> : null}
      {attention ? <div className="space-y-2 rounded-xl border border-line p-3"><p>{String(attention.question_text)}</p><p className="text-xs text-muted">{t('ui.sg.answerScope')}</p>
        <label className="block text-sm">{t('ui.sg.source')}<select className={field} value={(activeSource?.source_id as string) ?? ''} onChange={event => updateDraft({source:event.target.value,actor:'',thread:''})}><option value="">{t('ui.sg.choose')}</option>{options.map(source => <option key={String(source.source_id)} value={String(source.source_id)}>{String(source.source_id)}</option>)}</select></label>
        <label className="block text-sm">{t('ui.sg.actor')}<select className={field} value={selectedActor} onChange={event => updateDraft({actor:event.target.value})}><option value="">{t('ui.sg.choose')}</option>{actors.map(item => <option key={item}>{item}</option>)}</select></label>
        <label className="block text-sm">{t('ui.sg.thread')}<select className={field} value={selectedThread} onChange={event => updateDraft({thread:event.target.value})}><option value="">{t('ui.sg.choose')}</option>{threads.map(item => <option key={item}>{item}</option>)}</select></label>
        <label className="block text-sm">{t('ui.sg.answer')}<textarea className={field} value={answer} onChange={event => setAnswer(event.target.value)} /></label>
        <button type="button" className={button} disabled={busy || !answer.trim() || !activeSource || !selectedActor || !selectedThread} onClick={() => void execute('answer',{attention_id:attention.attention_id,text:answer,source_id:activeSource?.source_id,actor_id:selectedActor,thread_id:selectedThread})}>{t('ui.sg.answer')}</button></div> : null}
      {effect ? <div className="space-y-2 rounded-xl border border-line p-3"><p className="text-sm">{t('ui.sg.noReplay')}</p>
        <p className="break-all text-xs">{t('ui.sg.effect')}: {String(effect.effect_id)} · {t('ui.sg.generation')}: {String(effect.generation)}</p>
        <details><summary className="text-sm">{t('ui.sg.effect')}</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs">{String(effect.payload_json ?? '')}</pre></details>
        {effect.receipt_json ? <details><summary className="text-sm">{t('ui.sg.receipt')}</summary><pre className="max-h-64 overflow-auto whitespace-pre-wrap text-xs">{String(effect.receipt_json)}</pre></details> : null}
        <label className="block text-sm">{t('ui.sg.note')}<textarea className={field} value={note} onChange={event => setNote(event.target.value)} /></label>
        <button type="button" className={button} disabled={busy || !note.trim()} onClick={() => void execute('verify',{effect_id:effect.effect_id,generation:effect.generation,note})}>{t('ui.sg.verify')}</button></div> : null}
    </> : null}
  </section>
}
