import {expect,it,vi} from 'vitest'
import {readPreviewText} from '../filePreviewText'
const bytes=(...values:number[])=>new Uint8Array(values)
function reader(chunks:Uint8Array[]){return new ReadableStream<Uint8Array>({start(control){chunks.forEach(value=>control.enqueue(value));control.close()}}).getReader()}
it('decodes split Unicode while keeping the received source exact',async()=>{
 expect(await readPreviewText(reader([bytes(0xe4),bytes(0xb8,0xad,13,10)]),10,5)).toEqual({text:'中\r\n',truncated:false})
 expect(await readPreviewText(reader([bytes(0xef),bytes(0xbb,0xbf,65)]),10,4)).toEqual({text:'\ufeffA',truncated:false})
})
it('distinguishes a truncated UTF-8 prefix from illegal or incomplete source',async()=>{
 expect(await readPreviewText(reader([bytes(65,0xe4,0xb8,0xad)]),3,4)).toEqual({text:'A',truncated:true})
 await expect(readPreviewText(reader([bytes(65,0xff)]),10,2)).rejects.toThrow()
 await expect(readPreviewText(reader([bytes(65,0xe4)]),10,2)).rejects.toThrow()
})
it('checks for extra data when a stream ends a chunk exactly at the cap',async()=>{
 expect(await readPreviewText(reader([bytes(65,66),bytes(67)]),2,0)).toEqual({text:'AB',truncated:true})
 expect(await readPreviewText(reader([bytes(65,66)]),2,2)).toEqual({text:'AB',truncated:false})
})
it('retains read and cleanup failures together',async()=>{
 const original=new Error('read'),cleanup=new Error('cancel')
 const stub={read:vi.fn().mockRejectedValue(original),cancel:vi.fn().mockRejectedValue(cleanup)}
 const failure=await readPreviewText(stub as never,10,0).catch(error=>error)
 expect(failure.errors).toEqual([original,cleanup])
})
