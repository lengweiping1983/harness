import {hasUnpairedSurrogate} from './unicodeText'
import {ResourceError} from './resourceError'

export const CHART_KINDS = ['echarts', 'vega', 'vega-lite', 'chartjs', 'plotly'] as const
export type ChartKind = typeof CHART_KINDS[number]
export function isChartKind(value: string): value is ChartKind { return (CHART_KINDS as readonly string[]).includes(value) }
export const MAX_CHART_CHARS = 200000
const object = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value)

/** 仅接收有界 JSON；字符串从不作为 JavaScript 解释，外部资源不属于声明式图表输入。 */
export function parseChartSpec(source: string, kind: ChartKind): Record<string, unknown> {
  const fail = (key: string): never => {throw new ResourceError({key})}
  if (source.length > MAX_CHART_CHARS) fail('ui.chart.tooLarge')
  let spec: unknown
  try { spec = JSON.parse(source) } catch { return fail('ui.chart.invalidJson') }
  if (!object(spec)) return fail('ui.chart.invalidSpec')
  const tokens = /"(?:\\.|[^"\\])*"|[{}\[\],:]/g
  const stack: Array<{keys: Set<string> | null; key: boolean}> = []
  for (const token of source.matchAll(tokens)) {
    const text = token[0], top = stack.at(-1)
    if (text === '{' || text === '[') stack.push({keys: text === '{' ? new Set() : null, key: true})
    else if (text === '}' || text === ']') stack.pop()
    else if (text === ',') {if (top) top.key = true}
    else if (text.startsWith('"') && top?.keys && top.key) {
      const name = JSON.parse(text) as string
      if (top.keys.has(name)) fail('ui.chart.invalidJson')
      top.keys.add(name); top.key = false
    }
  }
  const visit = (value: unknown, depth: number): void => {
    if (depth > 48) fail('ui.chart.tooDeep')
    if (typeof value === 'string' && hasUnpairedSurrogate(value)) fail('ui.chart.invalidJson')
    if (typeof value === 'number' && !Number.isFinite(value)) fail('ui.chart.invalidJson')
    if (!value || typeof value !== 'object') return
    for (const [key, child] of Object.entries(value)) {
      if (hasUnpairedSurrogate(key)) fail('ui.chart.invalidJson')
      if (['__proto__', 'prototype', 'constructor'].includes(key)) fail('ui.chart.unsafeKey')
      if (['url', 'href', 'src'].includes(key) && typeof child === 'string') fail('ui.chart.inlineData')
      visit(child, depth + 1)
    }
  }
  visit(spec, 0)
  if ((kind === 'vega-lite' && !(spec.mark || spec.layer || spec.hconcat || spec.vconcat || spec.concat || spec.facet || spec.repeat)) ||
      (kind === 'vega' && !Array.isArray(spec.marks)) ||
      (kind === 'echarts' && !(Array.isArray(spec.series) || object(spec.series))) ||
      (kind === 'chartjs' && !(typeof spec.type === 'string' && object(spec.data))) ||
      (kind === 'plotly' && !Array.isArray(spec.data))) fail('ui.chart.invalidSpec')
  if (kind === 'plotly' && (spec.data as unknown[]).some(trace => !object(trace) || (trace.type !== undefined && (typeof trace.type !== 'string' || !['scatter', 'bar', 'pie'].includes(trace.type))))) fail('ui.chart.plotlyTypes')
  return spec
}

/** 序列化脚本中的数据，保证 </script> 和 HTML 注释不能突破数据边界。 */
const scriptData = (value: unknown) => JSON.stringify(value).replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029')
const inlineScript = (source: string) => source.replace(/<\/script/gi, '<\\/script').replace(/\/\/# sourceMappingURL=[^\n]*/g, '')

export function chartDocument(kind: ChartKind, spec: Record<string, unknown>, runtime: string): string {
  return `<!doctype html><html><head><style>html,body{margin:0}#chart{width:100%;height:360px;min-width:0}#chart canvas{max-width:100%}.vega-bindings{font:inherit}.role-axis-label text,.role-axis-title text,.role-legend-label text,.role-legend-title text{fill:var(--color-text-primary)}</style></head><body><div id="chart" role="img" aria-label="${kind}"></div><script>${inlineScript(runtime)}</script><script>
window.__harnessWidgetReady=(async()=>{
 const spec=${scriptData(spec)},kind=${scriptData(kind)},host=document.getElementById('chart');
 const styles=()=>getComputedStyle(document.documentElement),ink=()=>styles().getPropertyValue('--color-text-primary').trim()||'#334155';
 let resize=()=>{},theme=()=>{};
 if(kind==='echarts'){
  const chart=echarts.init(host,null,{renderer:'svg'});
  chart.setOption({animation:false,backgroundColor:'transparent',textStyle:{color:ink()},...spec});
  resize=()=>chart.resize();theme=()=>chart.setOption({textStyle:{color:ink()}});
 }else if(kind==='chartjs'){
  const canvas=document.createElement('canvas');host.append(canvas);
  Chart.defaults.color=ink();Chart.defaults.font.family=styles().getPropertyValue('--font-sans');
  const chart=new Chart(canvas,{...spec,options:{animation:false,responsive:true,maintainAspectRatio:false,...spec.options}});
  resize=()=>chart.resize();theme=()=>{Chart.defaults.color=ink();chart.update('none')};
 }else if(kind==='plotly'){
  await Plotly.newPlot(host,spec.data,{paper_bgcolor:'transparent',plot_bgcolor:'transparent',font:{color:ink()},...spec.layout},{...spec.config,responsive:true,displayModeBar:false,showLink:false});
  resize=()=>Plotly.Plots.resize(host);theme=()=>Plotly.relayout(host,{'font.color':spec.layout?.font?.color||ink()});
 }else{
  host.style.height='auto';
  const unavailable=()=>Promise.reject(Object.assign(new Error(),{harnessCopy:'ui.chart.inlineData'}));
  const loader={load:unavailable,sanitize:unavailable};
  const result=await vegaEmbed(host,{background:'transparent',...spec},{mode:kind,renderer:'svg',actions:false,tooltip:false,defaultStyle:false,ast:true,loader});
  resize=()=>result.view.resize().runAsync();
 }
 let queued=false,visible=true;
 const update=()=>{if(!visible||queued)return;queued=true;requestAnimationFrame(()=>{queued=false;if(visible)resize()})};
 const observer=new ResizeObserver(update);observer.observe(host);
 new MutationObserver(()=>{if(visible){theme();update()}}).observe(document.documentElement,{attributes:true,attributeFilter:['class','style']});
 addEventListener('message',event=>{if(event.source!==parent||event.data?.channel!=='harness-widget'||event.data?.kind!=='theme')return;visible=event.data.active===true;observer.disconnect();if(visible){observer.observe(host);update()}});
 addEventListener('pagehide',()=>observer.disconnect(),{once:true});
 host.dataset.chartReady='true';
})();
</script></body></html>`
}
