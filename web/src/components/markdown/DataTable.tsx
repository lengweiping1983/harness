import {memo,useMemo,useState} from 'react'
import {Download,Eye,Code,ChevronLeft,ChevronRight,ArrowUpDown} from 'lucide-react'
import {t,useLocale} from '@/i18n'
import {widgetFileName} from '@/lib/widgetPayload'
import {cn,download} from '@/lib/utils'
import {failureText} from '@/lib/resourceError'
import {noticeText} from '@/lib/noticeText'
import {parseTable,tableRows,TABLE_PAGE_SIZE,type TableKind} from '@/lib/tableData'
import {CopyButton,IconButton} from '@/components/ui/primitives'
import {CodeBlock} from './CodeBlock'

const initial=(source:string)=>({source,query:'',column:null as number|null,descending:false,page:0,header:true,showCode:false})
export const DataTable=memo(function DataTable({source,kind,closed=true,title,className}:{source:string;kind:TableKind;closed?:boolean;title?:string;className?:string}){
 const locale=useLocale()
 const [state,setState]=useState(()=>initial(source))
 const prefs=state.source===source?state:initial(source)
 const change=(patch:Partial<typeof state>)=>setState({...prefs,...patch,source})
 const parsed=useMemo(()=>{if(!closed)return null;try{return {rows:parseTable(source,kind)}}catch(error){return {error:failureText(error)}}},[source,kind,closed])
 const rows=parsed?.rows??[]
 const headings=prefs.header?rows[0]??[]:(rows[0]??[]).map((_,index)=>t('ui.table.column',{index:index+1}))
 const data=useMemo(()=>prefs.header?rows.slice(1):rows,[rows,prefs.header])
 const filtered=useMemo(()=>tableRows(data,prefs.query,prefs.column,prefs.descending,locale),[data,prefs.query,prefs.column,prefs.descending,locale])
 const page=Math.min(prefs.page,Math.max(0,Math.ceil(filtered.length/TABLE_PAGE_SIZE)-1)),start=page*TABLE_PAGE_SIZE
 const failure=parsed&&'error'in parsed?parsed.error:null
 return <figure className={cn('data-table overflow-hidden rounded-xl border border-line bg-surface',className)}>
  <figcaption className="flex min-h-9 items-center gap-2 border-b border-line px-3 py-1 text-xs">
   <span className="min-w-0 flex-1 truncate font-medium">{title??kind.toUpperCase()}</span>
   <IconButton size="xs" label={t(prefs.showCode?'ui.widget.viewWidget':'ui.widget.viewCode')} onClick={()=>change({showCode:!prefs.showCode})}>{prefs.showCode?<Eye className="h-3.5 w-3.5"/>:<Code className="h-3.5 w-3.5"/>}</IconButton>
   <CopyButton text={source}/>
   <IconButton size="xs" label={t('ui.widget.download')} onClick={()=>download(widgetFileName({title:title??'data',kind,code:source,chars:source.length,loadingMessages:[]}),source,kind==='csv'?'text/csv;charset=utf-8':'text/tab-separated-values;charset=utf-8')}><Download className="h-3.5 w-3.5"/></IconButton>
  </figcaption>
  {!closed?<p role="status" className="p-3 text-xs text-muted">{t('ui.table.receiving')}</p>:failure?<div><p role="alert" className="p-3 text-xs text-warn">{noticeText(failure)}</p><CodeBlock code={source} language={kind}/></div>:prefs.showCode?<CodeBlock code={source} language={kind}/>:<>
   <div className="flex flex-wrap items-center gap-3 border-b border-line px-3 py-2 text-xs">
    <input maxLength={256} aria-label={t('ui.table.filter')} placeholder={t('ui.table.filter')} value={prefs.query} onChange={event=>change({query:event.target.value,page:0})} className="min-w-0 flex-1 rounded-lg border border-line bg-canvas px-2 py-1 text-ink"/>
    <label className="inline-flex items-center gap-2"><input type="checkbox" checked={prefs.header} onChange={event=>change({header:event.target.checked,column:null,page:0})}/>{t('ui.table.header')}</label>
   </div>
   <div className="max-h-96 overflow-auto">
    <table className="w-full border-collapse text-left text-xs">
     <thead className="sticky top-0 bg-raised"><tr>{headings.map((heading,index)=><th key={index} scope="col" aria-sort={prefs.column===index?prefs.descending?'descending':'ascending':'none'} className="border-b border-line px-3 py-2 font-medium"><button type="button" aria-label={t('ui.table.sort',{column:heading||t('ui.table.column',{index:index+1})})} className="inline-flex w-full items-start gap-1 whitespace-pre-wrap text-left" onClick={()=>change({column:index,descending:prefs.column===index?!prefs.descending:false,page:0})}>{heading||t('ui.table.column',{index:index+1})}<ArrowUpDown aria-hidden className="mt-0.5 h-3 w-3 shrink-0 text-faint"/></button></th>)}</tr></thead>
     <tbody>{filtered.slice(start,start+TABLE_PAGE_SIZE).map(row=><tr key={row.index} className="border-b border-line last:border-0">{row.cells.map((cell,index)=><td key={index} className="whitespace-pre-wrap px-3 py-2 align-top [overflow-wrap:anywhere]">{cell}</td>)}</tr>)}</tbody>
    </table>
    {!filtered.length?<p role="status" className="p-3 text-xs text-muted">{t('ui.table.empty')}</p>:null}
   </div>
   <div className="flex items-center gap-2 border-t border-line px-3 py-2 text-xs text-muted">
    <span role="status" className="min-w-0 flex-1">{t('ui.table.summary',{start:filtered.length?start+1:0,end:Math.min(start+TABLE_PAGE_SIZE,filtered.length),total:filtered.length,columns:headings.length})}</span>
    <IconButton size="xs" label={t('ui.table.previous')} disabled={!page} onClick={()=>change({page:page-1})}><ChevronLeft className="h-3.5 w-3.5"/></IconButton>
    <IconButton size="xs" label={t('ui.table.next')} disabled={start+TABLE_PAGE_SIZE>=filtered.length} onClick={()=>change({page:page+1})}><ChevronRight className="h-3.5 w-3.5"/></IconButton>
   </div>
  </>}
 </figure>
})
