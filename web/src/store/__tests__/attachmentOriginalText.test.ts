import {afterEach, beforeEach, expect, test} from 'vitest'
import {blocksFromDetail, useChatStore} from '../chat'
import {setLocale} from '@/i18n'
import {userMessageText} from '@/lib/userMessageText'
import {matchesLocalEcho} from '@/lib/attachments'
import type {SessionDetail} from '@/types'

beforeEach(()=>useChatStore.setState({sessions:{}}))
afterEach(()=>setLocale('zh-CN'))
const attachment={attachmentId:'a'.repeat(64),name:'Original.txt',mediaType:'text/plain',size:5,isImage:false}

test('literal attachment markers remain original text in live messages, bubbles and snapshots',()=>{
 for(const marker of ['\n\n---\nThis message includes','\n\n---\n本条消息附带']){
  const original='Original before'+marker+' Original after'
  expect(userMessageText(original)).toBe(original)
  useChatStore.setState({sessions:{}})
  useChatStore.getState().appendLocalUser('owner',original,[attachment])
  useChatStore.getState().apply('owner',{type:'user/message',text:'Model-only attachment body',original_text:original,source_seq:1,attachments:[attachment]})
  const blocks=useChatStore.getState().get('owner').blocks
  expect(blocks).toHaveLength(1)
  expect(blocks[0]).toMatchObject({kind:'user',text:original})
  for(const language of ['en','zh-CN','en'] as const){
   setLocale(language)
   expect(useChatStore.getState().get('owner').blocks).toBe(blocks)
   const detail:SessionDetail={header:{id:'owner'},events:[],usage:{},stats:{},todos:[],timeline:[],
    messages:[{kind:'message',role:'user',source_seq:1,text:original,attachments:[attachment]}]}
   expect(blocksFromDetail(detail)[0]).toMatchObject({kind:'user',text:original})
  }
 }
})

test('attachment-only snapshots retain their user block and structured references',()=>{
 const detail:SessionDetail={header:{id:'owner'},events:[],usage:{},stats:{},todos:[],timeline:[],
  messages:[{kind:'message',role:'user',source_seq:1,text:'',attachments:[attachment]}]}
 expect(blocksFromDetail(detail)).toMatchObject([{kind:'user',text:'',attachments:[attachment]}])
})

test('a longer multiline message cannot consume an unrelated local echo',()=>{
 expect(matchesLocalEcho('Original','Original\nDifferent message')).toBe(false)
 expect(matchesLocalEcho('/goal Original','Original')).toBe(true)
 useChatStore.getState().appendLocalUser('owner','Original')
 useChatStore.getState().apply('owner',{type:'user/message',text:'Original\nDifferent message',source_seq:2})
 expect(useChatStore.getState().get('owner').blocks.map(block=>block.kind==='user'?block.text:null))
  .toEqual(['Original','Original\nDifferent message'])
})
