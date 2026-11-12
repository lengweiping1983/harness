import type {ChartKind} from './chartSpec'

// 文本包只在对应格式首次出现时请求；绝不在宿主执行图表库或模型配置。
const loaders = {
  echarts: () => import('../../node_modules/echarts/dist/echarts.min.js?raw').then(module => module.default),
  chartjs: () => import('../../node_modules/chart.js/dist/chart.umd.min.js?raw').then(module => module.default),
  plotly: () => import('../../node_modules/plotly.js-basic-dist-min/plotly-basic.min.js?raw').then(module => module.default),
  vega: () => Promise.all([
    import('../../node_modules/vega/build/vega.min.js?raw'), import('../../node_modules/vega-lite/build/vega-lite.min.js?raw'), import('../../node_modules/vega-embed/build/vega-embed.min.js?raw'),
  ]).then(modules => modules.map(module => module.default).join('\n;\n')),
}
const pending = new Map<string, Promise<string>>()
export function loadChartRuntime(kind: ChartKind): Promise<string> {
  const key = kind === 'vega-lite' ? 'vega' : kind
  let result = pending.get(key)
  if (!result) {
    result = loaders[key]().catch(error => {pending.delete(key); throw error})
    pending.set(key, result)
  }
  return result
}
