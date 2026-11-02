import {describe, expect, it} from 'vitest'
import {chartDocument, parseChartSpec, type ChartKind} from '../chartSpec'
import {parseWidgetPayload, widgetFileName} from '../widgetPayload'

const inputs: Record<ChartKind, object> = {
 echarts:{series:[{type:'bar',data:[1,2]}]},
 chartjs:{type:'line',data:{labels:['A','B'],datasets:[{data:[1,2]}]}},
 plotly:{data:[{type:'scatter',x:[1,2],y:[3,4]}]},
 vega:{marks:[]},
 'vega-lite':{mark:'bar',data:{values:[{x:'A',y:3}]}},
}
describe('declarative visualization inputs',()=>{
 it.each(Object.entries(inputs))('preserves %s source, tool payload and JSON download identity',(kind,input)=>{
  const source=JSON.stringify(input)
  expect(parseChartSpec(source,kind as ChartKind)).toEqual(input)
  const widget=parseWidgetPayload({widget:{title:'Original 图表',kind,code:source}})!
  expect(widget.code).toBe(source)
  expect(widgetFileName(widget)).toBe('Original_图表.'+kind+'.json')
 })
 it.each(['null','[]','false','{"series":[],"series":[]}','{"series":[],"x":{"a":1,"\\u0061":2}}','{"series":[],"x":1e999}','{"series":[],"__proto__":{}}','{"series":[],"data":{"url":"https://example.com/data"}}','{"series":[],"tooltip":{"href":"javascript:alert(1)"}}'])('rejects malformed/ambiguous/unsafe source: %s',source=>{
  expect(()=>parseChartSpec(source,'echarts')).toThrow()
 })
 it('accepts escaped source and adjacent object values without confusing values with keys',()=>{
  const spec={series:[{type:'bar',data:[1]}],description:'{"a":"a"} </script> "x":1',objects:[{a:1},{a:2}]}
  expect(parseChartSpec(JSON.stringify(spec),'echarts')).toEqual(spec)
 })
 it('enforces depth and size before chart execution',()=>{
  expect(()=>parseChartSpec('{"series":[],"nested":'+ '['.repeat(50)+'0'+']'.repeat(50)+'}','echarts')).toThrow()
  expect(()=>parseChartSpec(' '.repeat(200001),'echarts')).toThrow()
 })
 it('explicitly rejects unsupported Plotly trace families',()=>{
  expect(()=>parseChartSpec('{"data":[{"type":"scatter3d"}]}','plotly')).toThrow()
 })
 it('serializes hostile labels as data, and escapes library closing script sequences',()=>{
  const value='</script><script>parent.stolen=true</script>'
  const doc=chartDocument('echarts',{series:[],title:{text:value}},'const text="</script>";')
  expect(doc).not.toContain(value)
  expect(doc.match(/<script>/g)).toHaveLength(2)
  expect(doc).toContain('\\u003c/script>')
  expect(doc).toContain('<\\/script>')
 })
})

it('rejects escaped orphan Unicode and non-string Plotly trace types',()=>{
 expect(()=>parseChartSpec('{"series":[],"label":"\\ud800"}','echarts')).toThrow()
 expect(()=>parseChartSpec('{"data":[{"type":["bar"]}]}','plotly')).toThrow()
})
