import {afterEach, expect, it} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import postcss from 'postcss'
import tailwindcss from 'tailwindcss'
import tailwindConfig from '../../../tailwind.config.js'
import {ProcessGroupSummary, ProcessSummaryButton} from '@/components/chat/blocks/ProcessSummary'
import {TurnElapsed} from '@/components/chat/blocks/TurnElapsed'
import {setLocale, t} from '@/i18n'
import type {ToolRunRow} from '@/lib/toolRuns'

afterEach(() => setLocale('zh-CN'))

function row(count: number, open = false): ToolRunRow {
  return {kind:'tool-run', activityKind:'plan', id:'process', turn:1, at:1, open,
    label:'Original activity 原文', summary:'Original summary 原文', thoughts:2, count:8,
    running:count, failed:count, skipped:count, background:count}
}

const buttonClasses = (html: string) => new Set(html.match(/<button\b[^>]*class="([^"]+)"/)![1].split(/\s+/))
function content(html: string) {
  return html.match(/<span data-process-summary-content=""[^>]*>([\s\S]*)<\/span><svg\b/)![1]
}

it('轮次和过程摘要共用字号、字重与密度，展开不会加粗', async () => {
  const elapsed = renderToStaticMarkup(<ProcessSummaryButton expanded>
    <TurnElapsed activity={{turn:1,startSeq:1,startedAt:0,endSeq:2,endedAt:107000,reason:'completed'}} active={false} />
  </ProcessSummaryButton>)
  const grouped = renderToStaticMarkup(<ProcessGroupSummary row={row(1, true)} onClick={() => {}} />)
  const roots = [elapsed, grouped].map(buttonClasses)
  for (const classes of roots) {
    for (const name of ['text-xs','font-normal','leading-5','min-h-6','gap-1.5']) expect(classes.has(name)).toBe(true)
    expect(classes.has('activity-summary')).toBe(false)
    expect(classes.has('font-medium')).toBe(false)
    expect(classes.has('focus-visible:outline-brand')).toBe(true)
  }
  const css = await postcss([tailwindcss({...tailwindConfig, content:[{raw:elapsed + grouped, extension:'html'}],
    corePlugins:{preflight:false}})]).process('@tailwind utilities;', {from:undefined})
  for (const classes of roots) {
    const declarations: Record<string,string> = {}
    css.root.walkRules(rule => {
      if (classes.has(rule.selector.slice(1).replace(/\\(.)/g, '$1'))) {
        rule.walkDecls(declaration => {declarations[declaration.prop] = declaration.value})
      }
    })
    expect(declarations).toMatchObject({display:'flex','font-size':'var(--text-caption)',
      'font-weight':'400','line-height':'1.25rem','min-height':'1.5rem',gap:'0.375rem',
      'padding-top':'0.125rem','padding-bottom':'0.125rem'})
  }
})

it('状态与统计共用一条自然换行通道，没有强制第二行', async () => {
  const html = renderToStaticMarkup(<ProcessGroupSummary row={row(1)} onClick={() => {}} />)
  const body = content(html)
  // 主标签、统计和四种状态是同级内容，宽度不足时才由同一个容器换行。
  expect(body.match(/<span\b/g)).toHaveLength(6)
  expect(body).not.toMatch(/class="[^"]*\b(?:flex|block|w-full|basis-full)\b/)
  expect(body.match(/whitespace-nowrap/g)).toHaveLength(4)
  const classes = new Set(html.match(/data-process-summary-content="" class="([^"]+)"/)![1].split(/\s+/))
  const css = await postcss([tailwindcss({...tailwindConfig, content:[{raw:html, extension:'html'}],
    corePlugins:{preflight:false}})]).process('@tailwind utilities;', {from:undefined})
  const declarations: Record<string,string> = {}
  css.root.walkRules(rule => {
    if (classes.has(rule.selector.slice(1).replace(/\\(.)/g, '$1'))) {
      rule.walkDecls(declaration => {declarations[declaration.prop] = declaration.value})
    }
  })
  expect(declarations).toMatchObject({display:'flex','flex-wrap':'wrap','min-width':'0px','column-gap':'0.375rem'})
  expect(declarations['flex-basis']).toBeUndefined()
})

it('完整状态计数随语言切换，保留失败颜色、折叠语义和活动原文', () => {
  for (const count of [0,1,2]) {
    const group = row(count)
    const original = JSON.stringify(group)
    for (const locale of ['en','zh-CN','en'] as const) {
      setLocale(locale)
      for (const open of [false,true]) {
        const html = renderToStaticMarkup(<div className="dark"><ProcessGroupSummary row={{...group,open}} onClick={() => {}} /></div>)
        expect(html).toContain(`aria-expanded="${open}"`)
        expect(html).toContain('Original activity 原文')
        expect(html).toContain('Original summary 原文')
        for (const key of ['runningCount','failedCount','skippedCount','backgroundCount']) {
          expect(content(html).includes(t('ui.process.' + key,{count}))).toBe(count > 0)
        }
        if (count) {
          expect(content(html)).toMatch(/class="[^"]*text-danger[^\"]*"/)
          expect(html).toContain(locale === 'en' ? `${count} in progress` : `${count} 项进行中`)
        }
      }
    }
    expect(JSON.stringify(group)).toBe(original)
  }
  const disabled = renderToStaticMarkup(<ProcessSummaryButton disabled>Elapsed</ProcessSummaryButton>)
  expect(disabled).toContain('disabled=""')
  expect(disabled).not.toContain('aria-expanded')
  expect(disabled).not.toContain('lucide-chevron-right')
})
