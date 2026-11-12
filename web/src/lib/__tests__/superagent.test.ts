import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import * as sg from '../superagent'
import {setLocale} from '@/i18n'
import {noticeText} from '../noticeText'
import {failureText} from '../resourceError'
import {ApiError} from '../http'

const connection: sg.Connection = {connection_id:'sg_fixture',instance_id:'fixture',config_path:'/fixture.json',runtime:'stopped',error_code:null}
const envelope = (resource: string, data: sg.Row) => ({connection_id:connection.connection_id,instance_id:'fixture',resource,data:{instance_id:'fixture',...data}})
const page = (overrides: sg.Row = {}) => ({resource:'missions',mission_id:null,items:[{mission_id:'one',title:'Original'}],total:1,has_more:false,next_cursor:null,...overrides})

beforeEach(() => sg.useWorkbenchStore.setState({configPath:'',selected:'',connections:[],writes:{},drafts:{}}))
afterEach(async () => {vi.unstubAllGlobals(); await setLocale('en')})

it('returns validated connections through listing, connecting and state reads for every runtime', async () => {
  for (const runtime of ['running','stopped','failed','unavailable'] as const) {
    const expected = {...connection,runtime,error_code:runtime === 'failed' ? 'fixture/error' : null}
    const fetch = vi.fn(async (path: string, _options: RequestInit) => Response.json(path === '/api/v1/superagents' ? {connections:[expected]} : expected))
    vi.stubGlobal('fetch',fetch)
    await expect(sg.listConnections()).resolves.toEqual([expected])
    await expect(sg.connect(connection.config_path)).resolves.toEqual(expected)
    await expect(sg.connectionState(connection)).resolves.toEqual(expected)
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({config_path:connection.config_path})
  }
})

it('rejects malformed connection fields, duplicate identities and foreign connection states', async () => {
  for (const invalid of [
    {...connection,connection_id:' '}, {...connection,instance_id:''}, {...connection,config_path:' '},
    {...connection,runtime:'done'}, {...connection,error_code:false},
  ]) {
    vi.stubGlobal('fetch',vi.fn(async () => Response.json({connections:[invalid]})))
    await expect(sg.listConnections()).rejects.toBeDefined()
  }
  vi.stubGlobal('fetch',vi.fn(async () => Response.json({connections:[connection,connection]})))
  await expect(sg.listConnections()).rejects.toMatchObject({copy:{key:'ui.sg.error.protocol'}})
  for (const invalid of [{...connection,connection_id:'foreign'}, {...connection,instance_id:'foreign'}]) {
    vi.stubGlobal('fetch',vi.fn(async () => Response.json(invalid)))
    await expect(sg.connectionState(connection)).rejects.toMatchObject({copy:{key:'ui.sg.error.protocol'}})
  }
})

it('preserves generic API failure diagnostics without retrying when a workbench resource is absent or invalid', async () => {
  for (const details of [
    {reason:'fixture'}, {copy:{key:'ui.sg.error.not-registered'}},
    {copy:{key:'ui.sg.error.operation',params:{unexpected:'fixture'}}},
  ]) {
    const fetch = vi.fn(async () => Response.json({code:'fixture/denied',message:'Original diagnostic',details},{status:403}))
    vi.stubGlobal('fetch',fetch)
    const failure = await sg.connect(connection.config_path).catch(error => error)
    expect(failure).toBeInstanceOf(ApiError)
    expect(failure).toMatchObject({status:403,code:'fixture/denied',message:'Original diagnostic',details})
    expect(fetch).toHaveBeenCalledTimes(1)
  }
})

it('rejects a foreign instance, resource, duplicate identity and inconsistent terminal page before publishing it', async () => {
  for (const payload of [
    {...envelope('missions',page()),instance_id:'foreign'},
    envelope('jobs',page()),
    envelope('missions',page({items:[{mission_id:'same'},{mission_id:'same'}],total:2})),
    envelope('missions',page({items:[],total:1})),
    envelope('missions',page({has_more:true,next_cursor:null})),
  ]) {
    vi.stubGlobal('fetch',vi.fn(async () => Response.json(payload)))
    await expect(sg.page(connection,'missions')).rejects.toBeDefined()
  }
})

it('rejects cross-query, repeated cursor and overlapping appended pages while preserving the original objects', () => {
  const first = {instance_id:'fixture',...page({total:2,has_more:true,next_cursor:'next'})} as sg.Page
  const second = {...first,items:[{mission_id:'two'}],has_more:false,next_cursor:null}
  expect(sg.appendPage(first,second).items.map(row => row.mission_id)).toEqual(['one','two'])
  for (const bad of [{...second,instance_id:'foreign'},{...second,items:first.items},{...second,items:[]},
    {...second,has_more:true,next_cursor:'next'}]) expect(() => sg.appendPage(first,bad)).toThrow()
  expect(first.items).toHaveLength(1)
})

it('binds a write to its original request and blocks another write until its unknown result is checked without replay', async () => {
  const calls: {path:string;options:RequestInit}[] = []
  vi.stubGlobal('fetch',vi.fn(async (path: string,options: RequestInit) => {
    calls.push({path,options})
    if (options.method === 'POST') throw new TypeError('fixture lost response')
    const requestId = sg.useWorkbenchStore.getState().writes[connection.connection_id].requestId
    return Response.json(envelope('receipt',{request_id:requestId,state:'completed',receipt:{state:'applied'}}))
  }))
  await expect(sg.write(connection,'control',{action:'pause',mission_id:'original'})).rejects.toBeDefined()
  const original = sg.useWorkbenchStore.getState().writes[connection.connection_id]
  expect(original.unconfirmed).toBe(true)
  await expect(sg.write(connection,'control',{action:'resume',mission_id:'original'})).rejects.toBeDefined()
  expect(calls).toHaveLength(1)
  await sg.checkRequest(connection)
  expect(sg.useWorkbenchStore.getState().writes[connection.connection_id].unconfirmed).toBe(false)
  expect(calls.filter(item => item.options.method === 'POST')).toHaveLength(1)
  expect(JSON.parse(String(calls[0].options.body)).request_id).toBe(original.requestId)
})

it('keeps the same owned write alive after a view is gone and accepts only its exact returned identity', async () => {
  let release!: (value: Response) => void
  const fetch = vi.fn(() => new Promise<Response>(resolve => {release=resolve}))
  vi.stubGlobal('fetch',fetch)
  const pending = sg.write(connection,'submit',{text:'Original objective'})
  await expect(sg.write(connection,'submit',{text:'Another objective'})).rejects.toBeDefined()
  const requestId = sg.useWorkbenchStore.getState().writes[connection.connection_id].requestId
  release(Response.json({connection_id:connection.connection_id,instance_id:'fixture',request_id:requestId,receipt:{queued:true}}))
  await pending
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(sg.useWorkbenchStore.getState().writes[connection.connection_id].receipt).toEqual({queued:true})
})

it('retains explicit error resource identity for an English Chinese English roundtrip', async () => {
  vi.stubGlobal('fetch',vi.fn(async () => Response.json({code:'superagent/conflict',message:'Do not translate user text',
    details:{copy:{key:'ui.sg.error.operation'}}},{status:409})))
  let failure: unknown
  try {await sg.read(connection,'policy')} catch (error) {failure=failureText(error)}
  const labels=[]
  for (const language of ['en','zh-CN','en'] as const) {await setLocale(language); labels.push(noticeText(failure as any))}
  expect(labels[0]).toBe(labels[2]); expect(labels[0]).not.toBe(labels[1])
  expect(labels.join('')).not.toContain('Do not translate user text')
})
