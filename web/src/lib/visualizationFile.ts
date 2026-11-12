import {isChartKind,type ChartKind} from './chartSpec'
import type {TableKind} from './tableData'

/** 只按明确格式后缀选择预览，普通 JSON 不猜测为可执行或图表内容。 */
export function visualizationFile(path:string):ChartKind|TableKind|null {
 const name=path.split(/[\\/]/).at(-1)?.toLowerCase()??''
 if(name.endsWith('.csv'))return 'csv'
 if(name.endsWith('.tsv'))return 'tsv'
 const match=/\.([\w-]+)\.json$/.exec(name)
 return match&&isChartKind(match[1]!)?match[1]!:null
}
