import {create} from 'zustand'
import {ApiError, request} from './http'
import {ResourceError, failureText} from './resourceError'
import type {ResourceText} from '@/types'
import type {SuperagentConnection, SuperagentCommand, SuperagentWrite} from './protocol.generated'

export type Connection = SuperagentConnection
export type Row = Record<string, unknown>
export type Page = {instance_id: string; resource: string; mission_id: string | null; items: Row[]; total: number; has_more: boolean; next_cursor: string | null}
export type MissionDetail = {instance_id: string; mission: Row; counts: Record<string, number>; plan: Row | null; control: Row | null; watchdog: Row | null}
const root = '/api/v1/superagents'
export const isRow = (value: unknown): value is Row => !!value && typeof value === 'object' && !Array.isArray(value)
const identity = (value: unknown): value is string => typeof value === 'string' && !!value.trim()
const count = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
const bad = (): never => {throw new ResourceError({key: 'ui.sg.error.protocol'})}
const errorKeys = new Set(['ui.sg.error.local','ui.sg.error.operation','ui.sg.error.config','ui.sg.error.cursor',
  'ui.sg.error.missing','ui.sg.error.credentials','ui.sg.error.busy','ui.sg.error.invalid','ui.sg.error.uninitialized'])

async function call<T>(path: string, options: Parameters<typeof request>[1] = {}): Promise<T> {
  const result = await request<T>(path, options)
  if (!result.ok) {
    const details = result.details
    if (isRow(details) && isRow(details.copy) && typeof details.copy.key === 'string' && errorKeys.has(details.copy.key)
      && (details.copy.params === undefined || isRow(details.copy.params) && Object.keys(details.copy.params).length === 0)) {
      throw new ResourceError({key: details.copy.key})
    }
    throw new ApiError(result)
  }
  return result.data
}

function connection(value: unknown): Connection {
  if (!isRow(value)) return bad()
  const {connection_id, instance_id, config_path, runtime, error_code} = value
  if (!identity(connection_id) || !identity(instance_id) || !identity(config_path)
    || runtime !== 'running' && runtime !== 'stopped' && runtime !== 'failed' && runtime !== 'unavailable'
    || error_code !== null && typeof error_code !== 'string') return bad()
  return {connection_id, instance_id, config_path, runtime, error_code}
}
export async function listConnections(signal?: AbortSignal) {
  const data = await call<{connections: unknown[] }>(root, {signal})
  if (!isRow(data) || !Array.isArray(data.connections)) return bad()
  const rows = data.connections.map(connection)
  if (new Set(rows.map(row => row.connection_id)).size !== rows.length) return bad()
  return rows
}
export const connect = async (config_path: string) => connection(await call(root+'/connect', {method: 'POST', body: JSON.stringify({config_path})}))
export const connectionState = async (item: Connection, signal?: AbortSignal) => {
  const value = connection(await call(root+'/'+encodeURIComponent(item.connection_id), {signal}))
  if (value.connection_id !== item.connection_id || value.instance_id !== item.instance_id) return bad()
  return value
}
export async function read(item: Connection, resource: string, params: Record<string, string | number> = {}, signal?: AbortSignal): Promise<Row> {
  const query = new URLSearchParams({resource, ...Object.fromEntries(Object.entries(params).map(([key,value]) => [key,String(value)]))})
  const result = await call<Row>(root+'/'+encodeURIComponent(item.connection_id)+'/read?'+query, {signal})
  if (!isRow(result) || result.connection_id !== item.connection_id || result.instance_id !== item.instance_id || result.resource !== resource
    || !isRow(result.data) || result.data.instance_id !== item.instance_id) return bad()
  return result.data
}
const rowIds: Record<string, string> = {missions:'mission_id',jobs:'job_id',assignments:'assignment_id',artifacts:'artifact_id',reviews:'review_id',
  devices:'device_id',leases:'mission_id',audit:'event_id',mandates:'mandate_id',channels:'channel_id',sources:'source_instance_id',effects:'effect_id',attentions:'attention_id',inputs:'input_id',outbox:'message_id',events:'trigger_id',evaluations:'evaluation_id'}
export async function page(item: Connection, resource: string, mission?: string, cursor?: string, signal?: AbortSignal): Promise<Page> {
  const value = await read(item, resource, {...(mission ? {mission_id:mission} : {}),...(cursor ? {cursor} : {}),limit:32}, signal)
  if (value.resource !== resource || value.mission_id !== (mission ?? null) || !Array.isArray(value.items) || value.items.length > 32
    || !count(value.total) || value.items.length > value.total || typeof value.has_more !== 'boolean'
    || (value.has_more ? !identity(value.next_cursor) || !Array.isArray(value.items) || !value.items.length : value.next_cursor !== null)) return bad()
  if (!cursor && !value.has_more && value.items.length !== value.total) return bad()
  const rows = value.items as unknown[]
  if (!Object.hasOwn(rowIds,resource) || rows.some(row => !isRow(row) || !identity(row[rowIds[resource]]))) return bad()
  if (new Set(rows.map(row => (row as Row)[rowIds[resource]])).size !== rows.length) return bad()
  for (const row of rows as Row[]) {
    for (const key of ['body_json','config_json']) {
      if (Object.hasOwn(row,key)) {
        if (typeof row[key] !== 'string') return bad()
        let parsed: unknown
        try {parsed = JSON.parse(row[key] as string)} catch {return bad()}
        if (!isRow(parsed)) return bad()
        if (key === 'body_json' && (typeof parsed.text !== 'string' || parsed.attachments !== undefined
          && (!Array.isArray(parsed.attachments) || parsed.attachments.length > 16 || parsed.attachments.some(item => !isRow(item)
            || !identity(item.resource_key) || typeof item.name !== 'string' || !['image','file','audio','media'].includes(String(item.type)))))) return bad()
      }
    }
  }
  return value as Page
}
export const recordId = (resource: string, row: Row) => Object.hasOwn(rowIds,resource) && identity(row[rowIds[resource]]) ? String(row[rowIds[resource]]) : bad()
export function appendPage(previous: Page, next: Page): Page {
  const key = rowIds[previous.resource]
  if (!previous.has_more || previous.instance_id !== next.instance_id || previous.resource !== next.resource
    || previous.mission_id !== next.mission_id || previous.total !== next.total
    || next.next_cursor !== null && next.next_cursor === previous.next_cursor
    || previous.items.length + next.items.length > next.total
    || !next.has_more && previous.items.length + next.items.length !== next.total
    || previous.items.some(row => next.items.some(item => item[key] === row[key]))) return bad()
  return {...next, items: [...previous.items,...next.items]}
}
export async function mission(item: Connection, mission_id: string, signal?: AbortSignal): Promise<MissionDetail> {
  const value = await read(item,'mission',{mission_id},signal)
  if (!isRow(value.mission) || value.mission.mission_id !== mission_id || !isRow(value.counts)
    || Object.values(value.counts).some(value => !count(value)) || value.plan !== null && !isRow(value.plan)
    || value.control !== null && !isRow(value.control) || value.watchdog !== null && !isRow(value.watchdog)) return bad()
  return value as MissionDetail
}

type WriteState = {requestId: string; busy: boolean; unconfirmed: boolean; error: ResourceText; receipt: Row | null}
type State = {configPath: string; selected: string; connections: Connection[]; writes: Record<string, WriteState>;
  drafts: Record<string,{objective: string; source: string; actor: string; thread: string}>}
export const useWorkbenchStore = create<State>(() => ({configPath:'', selected:'', connections:[], writes:{},drafts:{}}))
export async function write(item: Connection, action: SuperagentCommand['action'], payload: Row): Promise<SuperagentWrite> {
  const current = useWorkbenchStore.getState().writes[item.connection_id]
  if (current?.busy || current?.unconfirmed) throw new ResourceError({key:'ui.sg.error.operation'})
  const requestId = crypto.randomUUID()
  const update = (value: WriteState) => useWorkbenchStore.setState(state => ({writes:{...state.writes,[item.connection_id]:value}}))
  update({requestId,busy:true,unconfirmed:false,error:'',receipt:null})
  try {
    const result = await call<SuperagentWrite>(root+'/'+encodeURIComponent(item.connection_id)+'/commands', {method:'POST',body:JSON.stringify({request_id:requestId,action,payload})})
    if (!isRow(result) || result.connection_id !== item.connection_id || result.instance_id !== item.instance_id
      || result.request_id !== requestId || !isRow(result.receipt)) return bad()
    update({requestId,busy:false,unconfirmed:false,error:'',receipt:result.receipt})
    return result
  } catch (failure) {
    update({requestId,busy:false,unconfirmed:true,error:failureText(failure),receipt:null})
    throw failure
  }
}
export async function checkRequest(item: Connection) {
  const previous = useWorkbenchStore.getState().writes[item.connection_id]
  if (!previous || previous.busy) return
  const receipt = await read(item,'receipt',{identifier:previous.requestId})
  if (receipt.request_id !== previous.requestId || !['pending','completed','accepted','rejected','applied','failed'].includes(String(receipt.state))) return bad()
  useWorkbenchStore.setState(state => ({writes:{...state.writes,[item.connection_id]:{...previous,
    unconfirmed:receipt.state === 'pending',receipt,error:''}}}))
}
export const mediaUrl = (item: Connection, mediaId: string) => {
  if (!/^media_[a-f0-9]{64}$/.test(mediaId)) return bad()
  return root+'/'+encodeURIComponent(item.connection_id)+'/media/'+mediaId
}

export const STATE_LABELS: Record<string,string> = {
  active:'ui.sg.state.active',planning:'ui.sg.state.planning',running:'ui.sg.state.running',integrating:'ui.sg.state.integrating',reviewing:'ui.sg.state.reviewing',
  cancelling:'ui.sg.state.cancelling',completed:'ui.sg.state.completed',blocked:'ui.sg.state.blocked',unknown:'ui.sg.state.unknown',
  rejected:'ui.sg.state.rejected',suppressed:'ui.sg.state.suppressed',cancelled:'ui.sg.state.cancelled',pending:'ui.sg.state.pending',
  accepted:'ui.sg.state.accepted',ready:'ui.sg.state.ready',checkpointed:'ui.sg.state.checkpointed',done:'ui.sg.state.done',
  succeeded:'ui.sg.state.succeeded',failed:'ui.sg.state.failed',interrupted:'ui.sg.state.interrupted',capturing:'ui.sg.state.capturing',
  submitting:'ui.sg.state.submitting',stopping:'ui.sg.state.stopping',stopped:'ui.sg.state.stopped',unavailable:'ui.sg.state.unavailable',
  healthy:'ui.sg.state.healthy',stalled:'ui.sg.state.stalled',open:'ui.sg.state.open',ended:'ui.sg.state.ended',sent:'ui.sg.state.sent',
  sending:'ui.sg.state.sending',retry_wait:'ui.sg.state.retry_wait',leased:'ui.sg.state.leased',filtered:'ui.sg.state.filtered',
  decided:'ui.sg.state.decided',answered:'ui.sg.state.answered',dismissed:'ui.sg.state.dismissed',
  granted:'ui.sg.state.granted',revalidated:'ui.sg.state.revalidated',revoked:'ui.sg.state.revoked',expired:'ui.sg.state.expired',affected:'ui.sg.state.affected',
  waiting:'ui.sg.state.waiting',read:'ui.sg.state.read',delivered:'ui.sg.state.delivered',handed_back:'ui.sg.state.handed_back',
  passed:'ui.sg.state.passed',invalid:'ui.sg.state.invalid',withdrawn:'ui.sg.state.withdrawn',
}
