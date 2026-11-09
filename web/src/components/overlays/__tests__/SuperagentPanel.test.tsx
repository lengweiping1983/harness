import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import {SuperagentPanel} from '../SuperagentPanel'
import * as sg from '@/lib/superagent'
import {setLocale,t} from '@/i18n'

const hooks = vi.hoisted(() => ({cursor:0,slots:[] as any[],effects:[] as (() => void)[],visible:true}))
vi.mock('react',async original => ({...await original<typeof import('react')>(),
  useRef:(value: unknown) => {const index=hooks.cursor++; return hooks.slots[index] ??= {current:value}},
  useState:(value: unknown) => {const index=hooks.cursor++; if (!(index in hooks.slots)) hooks.slots[index]=value;
    return [hooks.slots[index],(next: any) => {hooks.slots[index]=typeof next === 'function' ? next(hooks.slots[index]) : next}]},
  useEffect:(effect: () => void | (() => void),deps: unknown[]) => {
    const index=hooks.cursor++,previous=hooks.slots[index]
    if (!previous || deps.some((value,item) => value!==previous.deps[item])) {
      const next={deps,cleanup:previous?.cleanup}; hooks.slots[index]=next
      hooks.effects.push(() => {next.cleanup?.(); next.cleanup=effect()})
    }
  },
}))
vi.mock('@/i18n',async original => {const module=await original<typeof import('@/i18n')>(); return {...module,useLocale:module.getLocale}})
vi.mock('@/hooks/usePageVisible',() => ({usePageVisible:() => hooks.visible}))
vi.mock('@/lib/superagent',async original => {
  const module=await original<typeof import('@/lib/superagent')>()
  return {...module,useWorkbenchStore:Object.assign((selector: any) => selector(module.useWorkbenchStore.getState()),module.useWorkbenchStore)}
})

const a: sg.Connection={connection_id:'a',instance_id:'instance-a',config_path:'/a.json',runtime:'stopped',error_code:null}
const b: sg.Connection={connection_id:'b',instance_id:'instance-b',config_path:'/b.json',runtime:'stopped',error_code:null}
let writes: any[], reads: string[], delayed: ((resource: string,key: string,url: URL) => Promise<Response> | undefined) | undefined
let lostWrite=false
let writeReceipt: sg.Row
const unmount=() => {for (const slot of hooks.slots) slot?.cleanup?.(); hooks.slots=[];hooks.effects=[]}
const nodes=(value: any): any[] => Array.isArray(value) ? value.flatMap(nodes) : value && typeof value === 'object' && value.props ? [value,...nodes(value.props.children)] : []
const text=(value: any): string => Array.isArray(value) ? value.map(text).join('') : value?.props ? text(value.props.children) : typeof value === 'string' ? value : ''
function render(active=true) {hooks.cursor=0; const tree=SuperagentPanel({active}); for (const effect of hooks.effects.splice(0)) effect(); return tree}
const button=(key: string) => nodes(render()).find(node => text(node) === t(key) && (node.type === 'button' || typeof node.type === 'function'))
const envelope=(key: string,resource: string,data: sg.Row) => ({connection_id:key,instance_id:'instance-'+key,resource,data:{instance_id:'instance-'+key,...data}})
function page(key: string,resource: string,url: URL) {
  const more=!!url.searchParams.get('cursor')
  const items=Array.from({length:more ? 1 : 32},(_,index) => ({mission_id:key+'-'+(more ? 32 : index),title:'Objective '+key.toUpperCase()+(more ? 32 : index),state:'planning'}))
  return envelope(key,resource,{resource,mission_id:null,items,total:33,has_more:!more,next_cursor:more ? null : 'next-'+key})
}
async function loaded(key='A') {await vi.waitFor(() => expect(text(render())).toContain('Objective '+key+'0'))}

beforeEach(() => {
  unmount(); hooks.visible=true; writes=[]; reads=[]; delayed=undefined; lostWrite=false; writeReceipt={queued:true}
  sg.useWorkbenchStore.setState({configPath:'',selected:'a',connections:[a,b],writes:{},drafts:{}})
  vi.stubGlobal('fetch',vi.fn(async (path: string,options: RequestInit) => {
    const url=new URL(path,'http://fixture'),key=url.pathname.split('/')[4]
    if (options.method === 'POST') {
      const body=JSON.parse(String(options.body)); writes.push({path,body})
      if (lostWrite) throw new TypeError('fixture acknowledgement lost')
      return Response.json({connection_id:key,instance_id:'instance-'+key,request_id:body.request_id,receipt:writeReceipt})
    }
    reads.push(path)
    if (url.pathname === '/api/v1/superagents') return Response.json({connections:[a,b]})
    if (!url.pathname.endsWith('/read')) return Response.json(key === 'a' ? a : b)
    const resource=url.searchParams.get('resource') ?? ''
    const pending=delayed?.(resource,key,url); if (pending) return pending
    if (resource === 'local_options') return Response.json(envelope(key,resource,{sources:[{source_id:'local',actors:['operator'],threads:['work']}]}))
    if (resource === 'receipt') return Response.json(envelope(key,resource,{request_id:url.searchParams.get('identifier'),state:'completed',receipt:{state:'applied'}}))
    return Response.json(page(key,resource,url))
  }))
})

const onePage=(key: string,resource: string,row: sg.Row) => Response.json(envelope(key,resource,{resource,mission_id:null,items:[row],total:1,has_more:false,next_cursor:null}))

it('grants replies only to the selected original channel and source revision',async () => {
  await loaded()
  delayed=(resource,key) => resource==='channels' ? Promise.resolve(onePage(key,resource,{channel_id:'original-channel',source_instance_id:'github',source_revision:8,reply_enabled:0,address_json:'{"issue_number":"7"}'})) : undefined
  button('ui.sg.channels').props.onClick(); render()
  await vi.waitFor(() => expect(button('ui.sg.grantReply')).toBeDefined())
  button('ui.sg.grantReply').props.onClick()
  await vi.waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0].body).toMatchObject({action:'reply',payload:{channel_id:'original-channel',expected_source_revision:8,enabled:true}})
})

it('answers the exact attention through explicit local identity and thread',async () => {
  await loaded()
  delayed=(resource,key) => resource==='attentions' ? Promise.resolve(onePage(key,resource,{attention_id:'attention-original',state:'open',question_text:'Keep A or B?',options_json:'["A","B"]'})) : undefined
  button('ui.sg.attentions').props.onClick(); render()
  await vi.waitFor(() => expect(button('ui.sg.answer')).toBeDefined())
  button('ui.sg.answer').props.onClick(); render()
  nodes(render()).find(node => node.type==='textarea').props.onChange({target:{value:'Keep A.'}})
  const actions=nodes(render()).filter(node => node.type==='button' && text(node)===t('ui.sg.answer'))
  expect(actions.at(-1).props.disabled).toBeFalsy()
  actions.at(-1).props.onClick()
  await vi.waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0].body).toMatchObject({action:'answer',payload:{attention_id:'attention-original',text:'Keep A.',source_id:'local',actor_id:'operator',thread_id:'work'}})
})

it('fetches media only on explicit action and removes image previews while hidden',async () => {
  await loaded()
  delayed=(resource,key) => resource==='events' ? Promise.resolve(onePage(key,resource,{trigger_id:'media-original',body_json:JSON.stringify({text:'',attachments:[{type:'image',resource_key:'img_original',name:''}]})})) : undefined
  button('ui.sg.inputs').props.onClick(); render()
  await vi.waitFor(() => expect(button('ui.sg.fetchMedia')).toBeDefined())
  expect(nodes(render()).filter(node => node.type==='img')).toHaveLength(0)
  expect(writes).toHaveLength(0)
  writeReceipt={media_id:'media_'+'a'.repeat(64)}
  button('ui.sg.fetchMedia').props.onClick()
  await vi.waitFor(() => expect(nodes(render()).filter(node => node.type==='img')).toHaveLength(1))
  expect(writes[0].body).toMatchObject({action:'media',payload:{trigger_id:'media-original',resource_key:'img_original'}})
  expect(nodes(render()).find(node => node.type==='img').props.src).toBe('/api/v1/superagents/a/media/media_'+'a'.repeat(64))
  hooks.visible=false; render()
  expect(nodes(render()).filter(node => node.type==='img')).toHaveLength(0)
})

it('inspects the original effect before checking its exact generation without a replay command',async () => {
  await loaded()
  delayed=(resource,key) => resource==='effects' ? Promise.resolve(onePage(key,resource,{effect_id:'effect-original',generation:4,state:'unknown',kind:'outbox.send'}))
    : resource==='effect' ? Promise.resolve(Response.json(envelope(key,resource,{effect:{effect_id:'effect-original',generation:4,payload_json:'{"channel_id":"original-target"}',receipt_json:null}}))) : undefined
  button('ui.sg.effects').props.onClick(); render()
  await vi.waitFor(() => expect(button('ui.sg.verify')).toBeDefined())
  button('ui.sg.verify').props.onClick()
  await vi.waitFor(() => expect(text(render())).toContain('original-target'))
  expect(writes).toHaveLength(0)
  nodes(render()).find(node => node.type==='textarea').props.onChange({target:{value:'Inspect the original provider receipt.'}})
  const actions=nodes(render()).filter(node => node.type==='button' && text(node)===t('ui.sg.verify'))
  actions.at(-1).props.onClick()
  await vi.waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0].body).toMatchObject({action:'verify',payload:{effect_id:'effect-original',generation:4,note:'Inspect the original provider receipt.'}})
})

it('does not restore a late media result after leaving and returning to the same scope',async () => {
  await loaded()
  delayed=(resource,key) => resource==='events' ? Promise.resolve(onePage(key,resource,{trigger_id:'media-original',body_json:JSON.stringify({text:'',attachments:[{type:'image',resource_key:'img_original',name:''}]})})) : undefined
  button('ui.sg.inputs').props.onClick(); render()
  await vi.waitFor(() => expect(button('ui.sg.fetchMedia')).toBeDefined())
  const originalFetch=globalThis.fetch
  let release!: (response: Response) => void,accepted: any
  vi.stubGlobal('fetch',vi.fn((path: string,options: RequestInit) => {
    if (options.method==='POST') {accepted=JSON.parse(String(options.body));return new Promise<Response>(resolve => {release=resolve})}
    return originalFetch(path,options)
  }))
  button('ui.sg.fetchMedia').props.onClick()
  await vi.waitFor(() => expect(release).toBeTypeOf('function'))
  button('ui.sg.missions').props.onClick(); render()
  button('ui.sg.inputs').props.onClick(); render()
  release(Response.json({connection_id:'a',instance_id:'instance-a',request_id:accepted.request_id,receipt:{media_id:'media_'+'a'.repeat(64)}}))
  await vi.waitFor(() => expect(sg.useWorkbenchStore.getState().writes.a.busy).toBe(false))
  expect(nodes(render()).filter(node => node.type==='img')).toHaveLength(0)
})
afterEach(async () => {unmount();vi.unstubAllGlobals();await setLocale('en')})

it('loads the actual workbench and appends later pages without dropping earlier records',async () => {
  await loaded()
  button('ui.sg.more').props.onClick()
  await vi.waitFor(() => expect(text(render())).toContain('Objective A32'))
  expect(text(render())).toContain('Objective A0')
  expect(button('ui.sg.more')).toBeUndefined()
})

it('keeps local submission available when the ledger is uninitialized and translates its guidance without rereading',async () => {
  delayed=resource => resource === 'missions' ? Promise.resolve(Response.json({code:'superagent/status-unavailable',
    error:'Original server message',details:{copy:{key:'ui.sg.error.uninitialized'}}},{status:409})) : undefined
  await vi.waitFor(() => expect(text(render())).toContain(t('ui.sg.error.uninitialized')))
  const selectors=nodes(render()).filter(node => node.type === 'select')
  expect(selectors.map(node => node.props.value)).toEqual(['a','local','operator','work'])
  nodes(render()).find(node => node.type === 'textarea').props.onChange({target:{value:'Fix the original typo.'}})
  expect(button('ui.sg.submit').props.disabled).toBeFalsy()
  expect(button('ui.sg.start').props.disabled).toBeFalsy()
  expect(text(render())).not.toContain(t('ui.sg.empty'))
  const count=reads.length,labels=[]
  for (const locale of ['en','zh-CN','en'] as const) {
    await setLocale(locale)
    labels.push(text(nodes(render()).find(node => node.props.role === 'alert')))
    expect(labels.at(-1)).toBe(t('ui.sg.error.uninitialized'))
  }
  expect(labels[0]).toBe(labels[2]); expect(labels[0]).not.toBe(labels[1])
  expect(reads).toHaveLength(count); expect(writes).toHaveLength(0)
  delayed=undefined
  button('ui.sg.submit').props.onClick()
  await vi.waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0].body).toMatchObject({action:'submit',payload:{text:'Fix the original typo.',source_id:'local',actor_id:'operator',thread_id:'work'}})
  await loaded()
  expect(nodes(render()).find(node => node.props.role === 'alert')).toBeUndefined()
})

it('discards an old instance response and keeps objective drafts with their own instance',async () => {
  await loaded()
  const objective=nodes(render()).find(node => node.type === 'textarea')
  objective.props.onChange({target:{value:'Original A draft'}})
  let release!: (value: Response) => void
  delayed=(resource,key) => resource === 'missions' && key === 'a' ? new Promise(resolve => {release=resolve}) : undefined
  button('ui.sg.refresh').props.onClick()
  await vi.waitFor(() => expect(release).toBeTypeOf('function'))
  const selector=nodes(render()).find(node => node.type === 'select' && node.props.value === 'a')
  selector.props.onChange({target:{value:'b'}}); render()
  await loaded('B')
  release(Response.json(page('a','missions',new URL('http://fixture'))))
  await Promise.resolve();await Promise.resolve()
  expect(text(render())).toContain('Objective B0')
  expect(text(render())).not.toContain('Objective A0')
  expect(nodes(render()).find(node => node.type === 'textarea').props.value).toBe('')
  expect(sg.useWorkbenchStore.getState().drafts.a.objective).toBe('Original A draft')
})

it('pauses hidden reads and rejects a response that arrives after hiding',async () => {
  await loaded()
  let release!: (value: Response) => void
  delayed=resource => resource === 'missions' ? new Promise(resolve => {release=resolve}) : undefined
  button('ui.sg.refresh').props.onClick()
  await vi.waitFor(() => expect(release).toBeTypeOf('function'))
  hooks.visible=false; render()
  const count=reads.length
  release(Response.json(page('a','missions',new URL('http://fixture'))))
  await Promise.resolve();render();await Promise.resolve()
  expect(reads).toHaveLength(count)
  expect(writes).toHaveLength(0)
})

it('retains an accepted write across unmount and never submits it again when reopening',async () => {
  await loaded()
  nodes(render()).find(node => node.type === 'textarea').props.onChange({target:{value:'Fix the original typo.'}})
  lostWrite=true
  const submit=button('ui.sg.submit')
  expect(submit.props.disabled).toBeFalsy()
  submit.props.onClick()
  await vi.waitFor(() => expect(sg.useWorkbenchStore.getState().writes.a?.unconfirmed).toBe(true))
  const requestId=sg.useWorkbenchStore.getState().writes.a.requestId
  unmount(); render()
  expect(button('ui.sg.submit').props.disabled).toBe(true)
  expect(writes).toHaveLength(1)
  button('ui.sg.checkRequest').props.onClick()
  await vi.waitFor(() => expect(sg.useWorkbenchStore.getState().writes.a.unconfirmed).toBe(false))
  expect(writes).toHaveLength(1)
  expect(sg.useWorkbenchStore.getState().writes.a.requestId).toBe(requestId)
})

it('consumes English Chinese English labels in the same panel without refetching or changing user text',async () => {
  await loaded()
  nodes(render()).find(node => node.type === 'textarea').props.onChange({target:{value:'用户原文 unchanged'}})
  const count=reads.length,labels=[]
  for (const locale of ['en','zh-CN','en'] as const) {await setLocale(locale);labels.push(text(render()))}
  expect(labels[0]).toBe(labels[2]);expect(labels[0]).not.toBe(labels[1])
  expect(nodes(render()).find(node => node.type === 'textarea').props.value).toBe('用户原文 unchanged')
  expect(reads).toHaveLength(count)
  expect(writes).toHaveLength(0)
})

it('shows device ownership and the original lease without exposing credentials',async () => {
  await loaded()
  delayed=(resource,key) => resource==='devices' ? Promise.resolve(onePage(key,resource,{device_id:'worker-a',endpoint:'https://worker.invalid',capacity:2,identity_hash:'bound-identity'}))
    : resource==='leases' ? Promise.resolve(onePage(key,resource,{mission_id:'original-mission',device_id:'worker-a',state:'expired',generation:1,expires_at:1})) : undefined
  button('ui.sg.devices').props.onClick();render()
  await vi.waitFor(() => expect(text(render())).toContain('worker-a'))
  expect(text(render())).toContain('https://worker.invalid')
  button('ui.sg.leases').props.onClick();render()
  await vi.waitFor(() => expect(text(render())).toContain('original-mission'))
  expect(text(render())).toContain(t('ui.sg.state.expired'))
  const count=reads.length,labels=[]
  for (const locale of ['en','zh-CN','en'] as const) {await setLocale(locale);labels.push(text(render()))}
  expect(labels[0]).toBe(labels[2]);expect(labels[0]).not.toBe(labels[1]);expect(reads).toHaveLength(count)
  expect(writes).toHaveLength(0)
})

it('reads a mission lease only within the selected mission detail',async () => {
  await loaded()
  delayed=(resource,key,url) => {
    const mission=url.searchParams.get('mission_id')
    if (resource==='mission') return Promise.resolve(Response.json(envelope(key,resource,{mission:{mission_id:mission,title:'Selected mission'},counts:{},plan:null,control:null,watchdog:null})))
    if (mission) return Promise.resolve(Response.json(envelope(key,resource,{resource,mission_id:mission,items:resource==='leases' ? [{mission_id:mission,device_id:'owned-device',state:'active',generation:1}] : [],total:resource==='leases' ? 1 : 0,has_more:false,next_cursor:null})))
  }
  button('ui.sg.details').props.onClick();render()
  button('ui.sg.leases').props.onClick();render()
  await vi.waitFor(() => expect(text(render())).toContain('owned-device'))
  expect(reads.some(path => {const url=new URL(path,'http://fixture');return url.searchParams.get('resource')==='leases' && url.searchParams.get('mission_id')==='a-0'})).toBe(true)
  expect(writes).toHaveLength(0)
})
