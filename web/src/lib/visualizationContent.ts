import {isChartLang,scanFences,segmentStream} from './streaming'

const cache=new WeakMap<object,{text:string;visual:boolean}>()
/** 可视化是已交付正文；后续的简短总结不能把它归入默认收起的执行过程。 */
export function hasVisualization(block:{text:string}):boolean{
 const previous=cache.get(block)
 if(previous?.text===block.text)return previous.visual
 const visual=scanFences(block.text).some(fence=>fence.renderer||isChartLang(fence.lang)) ||
  (/<svg\b/i.test(block.text)&&segmentStream(block.text).some(segment=>segment.kind==='chart'))
 cache.set(block,{text:block.text,visual})
 return visual
}
