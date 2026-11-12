import {describe,it,expect} from 'vitest'
import {validSearchResult} from '../conversationSearch'
const hit = (seq: number) => ({source_seq:seq,source_time:1,role:'user' as const,prefix:'',match:'Original',suffix:' raw',start_char:0})
const page = () => ({session_id:'fixture',query:'Original',through:20,total:3,results:[hit(15),hit(10)],has_more:true,next_before:10})
describe('search receipt request identity',()=>{
 it('accepts current initial and pinned older pages without rewriting raw text',()=>{
  expect(validSearchResult(page(),'fixture','Original')).toBe(true)
  expect(validSearchResult({...page(),results:[hit(5)],has_more:false,next_before:null},'fixture','Original',20,10,3)).toBe(true)
  expect(validSearchResult({...page(),query:'Original'},'fixture','\u001c\u0085Original\u3000')).toBe(true)
 })
 it('rejects another conversation even when query and pagination are otherwise valid',()=>{
  expect(validSearchResult({...page(),session_id:'other'},'fixture','Original')).toBe(false)
 })
 it.each([
  {session_id:'other'}, {session_id:undefined}, {query:'Other'}, {through:19}, {total:2}, {results:[hit(10)]},
  {results:[hit(5),hit(5)]}, {results:[hit(5),hit(7)]}, {results:[hit(21)]},
  {results:[{...hit(5),source_time:NaN}]}, {results:[{...hit(5),start_char:-1}]},
  {results:[{...hit(5),match:''}]}, {results:[]}, {next_before:1},
  {has_more:false,next_before:10},
 ])('rejects mismatched or malformed pinned page %j',changes=>{
  expect(validSearchResult({...page(),...changes} as ReturnType<typeof page>,'fixture','Original',20,10,3)).toBe(false)
 })
 it('accepts an empty search with no events',()=>{
  expect(validSearchResult({session_id:'fixture',query:'Original',through:-1,total:0,results:[],has_more:false,next_before:null},'fixture','Original')).toBe(true)
 })
})
