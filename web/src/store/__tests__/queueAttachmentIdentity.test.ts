import {afterEach, beforeEach, expect, test} from 'vitest'
import {useDraftStore} from '../drafts'
import {validPersistedDrafts} from '@/lib/draftPersistence'
import {setLocale} from '@/i18n'

beforeEach(()=>useDraftStore.setState({bySession:{},submissions:{}}))
afterEach(()=>setLocale('zh-CN'))
const attachment={attachmentId:'a'.repeat(64),name:'Original.txt',mediaType:'text/plain',size:5,isImage:false}

test('withdrawal restores complete original text and independent removable attachment references',()=>{
 for(const language of ['en','zh-CN','en'] as const){
  setLocale(language)
  useDraftStore.setState({bySession:{},submissions:{}})
  const original='Original\n\n---\nThis message includes user text\n\n---\n本条消息附带 User text'
  useDraftStore.getState().restoreQueuedText('owner',original,[attachment])
  const restored=useDraftStore.getState().bySession.owner
  expect(restored.text).toBe(original)
  expect(restored.attachments).toMatchObject([attachment])
  expect(Object.keys(restored).sort()).toEqual(['attachments','text'])
  useDraftStore.getState().updateAttachments('owner',()=>[])
  expect(useDraftStore.getState().bySession.owner.text).toBe(original)
  expect(useDraftStore.getState().bySession.owner.attachments).toEqual([])
 }
})

test('the retired hidden attachment envelope is rejected rather than migrated or merged',()=>{
 const envelope={state:{bySession:{owner:{text:'Original',attachments:[{...attachment,localId:'original'}]}},submissions:{}},version:0}
 expect(validPersistedDrafts(JSON.stringify(envelope))).toBe(true)
 const original=JSON.stringify({...envelope,state:{...envelope.state,bySession:{owner:{...envelope.state.bySession.owner,attachmentContext:'Original retired payload'}}}})
 expect(validPersistedDrafts(original)).toBe(false)
 expect(JSON.parse(original).state.bySession.owner.attachmentContext).toBe('Original retired payload')
})
