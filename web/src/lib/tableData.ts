import {hasUnpairedSurrogate} from './unicodeText'
import {ResourceError} from './resourceError'

export type TableKind = 'csv' | 'tsv'
export function isTableKind(value: string): value is TableKind {return value === 'csv' || value === 'tsv'}
const fail = (key: string): never => {throw new ResourceError({key})}
export const TABLE_PAGE_SIZE = 50

/** 有界单遍解析；保留空格、引号和单元格内换行，绝不把数据当 HTML 或脚本。 */
export function parseTable(source: string, kind: TableKind): string[][] {
 if (source.length > 200000) fail('ui.table.tooLarge')
 if (hasUnpairedSurrogate(source)) fail('ui.table.invalid')
 if (!source) return []
 const delimiter = kind === 'csv' ? ',' : '\t'
 const text = source.replace(/^\uFEFF/, '')
 if (!text) return []
 const rows: string[][] = []
 let row: string[] = [], cell = '', quoted = false, endedQuote = false, cells = 0
 const field = () => {row.push(cell);cell='';endedQuote=false;if (++cells > 50000 || row.length > 100) fail('ui.table.tooLarge')}
 const record = () => {field();if (rows.length && row.length !== rows[0]!.length) fail('ui.table.columns');rows.push(row);row=[];if (rows.length > 10000) fail('ui.table.tooLarge')}
 for (let i=0;i<text.length;i++) {
  const char=text[i]!
  if (quoted) {
   if (char === '"') {if (text[i+1] === '"') {cell+='"';i++} else {quoted=false;endedQuote=true}}
   else cell+=char
  } else if (char === delimiter) field()
  else if (char === '\n' || char === '\r') {record();if (char === '\r' && text[i+1] === '\n') i++}
  else if (char === '"') {if (cell || endedQuote) fail('ui.table.invalid');quoted=true}
  else {if (endedQuote) fail('ui.table.invalid');cell+=char}
 }
 if (quoted) fail('ui.table.invalid')
 if (cell || row.length || endedQuote || !/[\r\n]$/.test(text)) record()
 return rows
}

export function tableRows(rows: string[][], query: string, column: number | null, descending: boolean, locale: string) {
 const needle=query.trim().toLocaleLowerCase(locale)
 const result=rows.map((cells,index)=>({cells,index})).filter(row=>!needle || row.cells.some(cell=>cell.toLocaleLowerCase(locale).includes(needle)))
 if (column !== null) {
  const collator=new Intl.Collator(locale,{numeric:true,sensitivity:'base'})
  const number=/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i
  result.sort((a,b)=>{
   const left=a.cells[column]??'',right=b.cells[column]??''
   const leftNumber=number.test(left.trim())&&Number.isFinite(Number(left)),rightNumber=number.test(right.trim())&&Number.isFinite(Number(right))
   const order=leftNumber&&rightNumber ? Number(left)-Number(right) : leftNumber!==rightNumber ? leftNumber?-1:1 : collator.compare(left,right)
   return (descending?-order:order)||a.index-b.index
  })
 }
 return result
}
