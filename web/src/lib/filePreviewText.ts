/** 读取有界 UTF-8 前缀；仅截断边界允许保留解码器里的未完成码点，非法原文必须报错。 */
export async function readPreviewText(reader:ReadableStreamDefaultReader<Uint8Array>,limit:number,total:number):Promise<{text:string;truncated:boolean}>{
 const decoder=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true})
 let text='',used=0,truncated=Number.isFinite(total)&&total>limit
 let failed=false,failure:unknown
 try{
  for(;;){
   const chunk=await reader.read()
   if(chunk.done)break
   const available=limit-used
   text+=decoder.decode(chunk.value.subarray(0,available),{stream:true})
   used+=Math.min(available,chunk.value.length)
   if(chunk.value.length>available){truncated=true;break}
  }
  if(!truncated)text+=decoder.decode()
 }catch(error){failed=true;failure=error}
 try{await reader.cancel()}catch(error){failure=failed?new AggregateError([failure,error]):error;failed=true}
 if(failed)throw failure
 return {text,truncated}
}
