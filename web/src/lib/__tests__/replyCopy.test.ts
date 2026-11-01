import {afterEach, describe, expect, it, vi} from 'vitest'
import type {ConversationMessagePart} from '@/types'
import {readReply} from '../turnCompletion'
import {setLocale,t} from '@/i18n'
import {ResourceError,failureText} from '../resourceError'
import {noticeText} from '../noticeText'
afterEach(()=>setLocale('zh-CN'))

const part = (values: Partial<ConversationMessagePart> = {}): ConversationMessagePart => ({
  session_id: 'fixture', source_seq: 7, source_time: 1, role: 'assistant', text: '你好😀', start: 0, end: 3,
  total_chars: 3, previous_start: null, next_start: null, ...values,
})

describe('完整回复复制的分页证据', () => {
  it('按 Unicode 码点计数，完整拼接多页而不遗漏字符', async () => {
    const read = vi.fn().mockResolvedValueOnce(part({next_start: 3, total_chars: 5}))
      .mockResolvedValueOnce(part({text: '尾声', start: 3, end: 5, total_chars: 5, previous_start: 0}))
    await expect(readReply('fixture',7, read)).resolves.toBe('你好😀尾声')
    expect(read.mock.calls).toEqual([[0], [3]])
  })

  it.each([
    {session_id:'other'}, {session_id:undefined}, {text: '你'}, {end: 4}, {total_chars: 2}, {end: 3.5}, {total_chars: Number.NaN},
    {next_start: 3, total_chars: 3}, {previous_start: 0}, {start: -1}, {source_seq: 8}, {role: 'user' as const},
  ])('拒绝不一致的范围与正文：%j', async value => {
    const read = vi.fn().mockResolvedValue(part(value))
    await expect(readReply('fixture',7, read)).rejects.toThrow()
    expect(read).toHaveBeenCalledTimes(1)
  })
})

it('无效分页保留错误资源，切换语言不能再次读取或复制',async()=>{
 setLocale('en')
 const read=vi.fn().mockResolvedValue(part({source_seq:8}))
 const error=await readReply('fixture',7,read).catch(reason=>reason)
 expect(error).toBeInstanceOf(ResourceError)
 const copy=failureText(error)
 for(const locale of ['en','zh-CN','en'] as const){setLocale(locale);expect(noticeText(copy)).toBe(t('ui.lib.turnCompletion.cf5425f62d93'))}
 expect(read).toHaveBeenCalledTimes(1)
})
it('后续分页的总长变化时拒绝返回部分正文',async()=>{
 const read=vi.fn().mockResolvedValueOnce(part({next_start:3,total_chars:5}))
  .mockResolvedValueOnce(part({text:'尾声',start:3,end:5,total_chars:6,previous_start:0,next_start:5}))
 await expect(readReply('fixture',7,read)).rejects.toBeInstanceOf(ResourceError)
 expect(read.mock.calls).toEqual([[0],[3]])
})

it('拒绝不能后退的上一页游标且不返回已读取的部分正文',async()=>{
 const read=vi.fn().mockResolvedValueOnce(part({next_start:3,total_chars:5}))
  .mockResolvedValueOnce(part({text:'尾声',start:3,end:5,total_chars:5,previous_start:3}))
 await expect(readReply('fixture',7,read)).rejects.toBeInstanceOf(ResourceError)
 expect(read.mock.calls).toEqual([[0],[3]])
})

it('后续页来自另一对话时不返回任何部分正文',async()=>{
 const read=vi.fn().mockResolvedValueOnce(part({next_start:3,total_chars:5}))
  .mockResolvedValueOnce(part({session_id:'other',text:'尾声',start:3,end:5,total_chars:5,previous_start:0}))
 await expect(readReply('fixture',7,read)).rejects.toBeInstanceOf(ResourceError)
 expect(read.mock.calls).toEqual([[0],[3]])
})
