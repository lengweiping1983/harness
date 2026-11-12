import {expect,it} from 'vitest'
import {parseTable,tableRows} from '../tableData'
import {visualizationFile} from '../visualizationFile'
import {parseWidgetPayload,widgetFileName} from '../widgetPayload'

it('preserves quoted delimiters, escaped quotes, Unicode, multiline cells and terminal empty cells',()=>{
 expect(parseTable('\ufeffname,value\r\n"A,B","quoted ""word""\r\n下一行"\r\nempty,\r\n','csv')).toEqual([['name','value'],['A,B','quoted "word"\r\n下一行'],['empty','']])
 expect(parseTable('a\tb\n"x\ty"\t"z\nz"','tsv')).toEqual([['a','b'],['x\ty','z\nz']])
 expect(parseTable('""','csv')).toEqual([['']])
 expect(parseTable('','csv')).toEqual([])
})
it.each(['a,b\n"open,b','a,b\nx"y,z','a,b\n"x" q,z','a,b\nx','a,b\nx,y,z'])('rejects damaged records without showing partial tables: %s',source=>expect(()=>parseTable(source,'csv')).toThrow())
it('bounds cells, rows, columns and total source size',()=>{
 for(const source of ['x'.repeat(200001),'x\n'.repeat(10001),'a,'.repeat(100)+'a','a,b,c,d,e,f\n'.repeat(9000)])expect(()=>parseTable(source,'csv')).toThrow()
})
it('sorts numerical and textual cells stably without rewriting original data',()=>{
 const rows=[['A','10'],['C','-2.5'],['B','3'],['D','3']]
 expect(tableRows(rows,'',1,false,'en').map(row=>row.cells[0])).toEqual(['C','B','D','A'])
 expect(tableRows(rows,'b',null,false,'en')).toEqual([{cells:['B','3'],index:2}])
 expect(rows[0]).toEqual(['A','10'])
 const mixed=[['-2'],['-10x'],['-10'],[''],['3']]
 expect(tableRows(mixed,'',0,false,'en').map(row=>row.cells[0])).toEqual(['-10','-2','3','','-10x'])
 expect(tableRows(mixed,'',0,true,'en').map(row=>row.cells[0])).toEqual(['-10x','','3','-2','-10'])
})
it('requires explicit chart file suffixes and preserves original table tool payloads',()=>{
 expect(visualizationFile('/a/sales.echarts.json')).toBe('echarts')
 expect(visualizationFile('data.json')).toBeNull()
 expect(visualizationFile('data.json.exe')).toBeNull()
 expect(visualizationFile('report.CSV')).toBe('csv')
 const source='name\tvalue\r\n原文\t001\r\n'
 const payload=parseWidgetPayload({widget:{title:'Data / report',kind:'tsv',code:source}})!
 expect(payload.code).toBe(source)
 expect(widgetFileName(payload)).toBe('Data__report.tsv')
})

it('rejects orphan surrogate values and counts non-BMP characters against the actual source limit',()=>{
 expect(()=>parseTable('a\n\uD800','csv')).toThrow()
 expect(()=>parseTable('😀'.repeat(100001),'csv')).toThrow()
 expect(parseTable('value\n😀','csv')).toEqual([['value'],['😀']])
})
