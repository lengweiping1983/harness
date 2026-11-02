import {afterEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {setLocale} from '@/i18n'
import {weekdayLabels, scheduleLabel} from '../automations'
import {mainConversationBlocks} from '../memberDock'
import {makeTeamAddress, type AgentMountState} from '../agentMount'
import {TeamRecipientPicker} from '@/components/chat/TeamRecipientPicker'
import {AssistantBlock, TeamBlock as TeamCard} from '@/components/chat/blocks/MessageBlocks'
import type {TeamBlock} from '@/types'

// 静态渲染不运行延迟加载的 effect；在此使用真实正文渲染器，保留原文断言。
// 异步加载及同实例语言往返由 member-preview-smoke.mjs 验证。
vi.mock('@/components/markdown/Markdown', async importOriginal => ({
  ...await importOriginal<typeof import('@/components/markdown/Markdown')>(),
  Markdown: (await import('@/components/markdown/MarkdownContent')).Markdown,
}))

afterEach(() => setLocale('zh-CN'))
it('uses weekday meanings in both locales, including repeated language switches', () => {
  for (const language of ['en','zh-CN','en'] as const) {
    setLocale(language)
    expect(weekdayLabels()).toEqual(language === 'en' ? ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'] : ['一','二','三','四','五','六','日'])
    expect(scheduleLabel({kind:'weekly',days:[0,2,6],time:'09:00'})).toBe(language === 'en' ? 'Every Mon, Wed, Sun 09:00' : '每周一、三、日 09:00')
    expect(scheduleLabel({kind:'monthly',monthdays:[1,15],time:'09:00'})).toBe(language === 'en' ? 'Monthly on day 1, 15 at 09:00' : '每月 1、15 号 09:00')
    expect(scheduleLabel({kind:'yearly',months:[1,7],monthdays:[1],time:'09:00'})).toBe(language === 'en' ? 'Yearly in month 1, 7 on day 1 at 09:00' : '每年 1、7 月 1 号 09:00')
  }
})
it.each(['en','zh-CN'] as const)('renders team assignments and recipient names with %s grammar', language => {
  setLocale(language)
  const members = [{name:'lead',displayName:'Lead',role:'lead',status:'running'}, {name:'worker',displayName:'Worker',role:'member',status:'pending'}]
  const team = {id:'team',kind:'team',runId:'run',team:'fixture',at:1,turn:1,phase:'execute',members,assignments:{worker:'Original task 原文'}} as TeamBlock
  const [plan] = mainConversationBlocks([team])
  expect(plan.kind).toBe('assistant')
  expect('text' in plan && plan.text).toBe(language === 'en' ? '- Worker (Waiting): Original task 原文' : '- Worker（等待中）：Original task 原文')
  const mount: AgentMountState = {available:true,agent:'fixture',agent_type:'team',revision:1,members:members.map((member,index) => ({name:member.name,display_name:member.displayName,role:member.role as 'lead'|'member',identity:String(index).repeat(64)}))}
  const html = renderToStaticMarkup(<TeamRecipientPicker mount={mount} address={makeTeamAddress(mount,['lead','worker'])} onChange={() => {}} />)
  expect(html).toContain(language === 'en' ? 'Lead, Worker' : 'Lead、Worker')
})

it('renders team member roles, errors and dependency punctuation in the current language', () => {
  const block = {id:'team',kind:'team',runId:'run',team:'fixture',at:1,turn:1,phase:'execute',members:[
    {name:'lead',displayName:'Lead 原文',role:'lead',status:'running'},
    {name:'worker',displayName:'Worker',role:'member',status:'pending',depends_on:['lead','other']},
    {name:'other',displayName:'Other',role:'member',status:'failed',error:'Original failure 原文'},
  ],assignments:{}} as TeamBlock
  for (const language of ['en','zh-CN','en'] as const) {
    setLocale(language)
    const html = renderToStaticMarkup(<TeamCard block={block} />)
    expect(html).toContain(language === 'en' ? 'title="Lead 原文 (Coordinator)"' : 'title="Lead 原文（协调者）"')
    expect(html).toContain(language === 'en' ? 'title="Worker (Member) ← Lead 原文, Other"' : 'title="Worker（成员） ← Lead 原文、Other"')
    expect(html).toContain(language === 'en' ? 'title="Other: Original failure 原文"' : 'title="Other：Original failure 原文"')
  }
})

it('resolves hook option tables and full guidance after language switches without rewriting saved commands', async () => {
  const {hookEvents,hookStatuses,addHookTemplate} = await import('../projectHooks')
  const {readHookForm} = await import('../hookForm')
  const {HookFormEditor} = await import('@/components/overlays/HookFormEditor')
  let saved = ''
  for (const language of ['en','zh-CN','en'] as const) {
    setLocale(language)
    expect(hookStatuses.failed).toBe(language === 'en' ? 'Failed' : '失败')
    const text = addHookTemplate('{"hooks":{}}',0)
    if (!saved) saved = text
    const html = renderToStaticMarkup(<HookFormEditor text={text} form={readHookForm(text)} disabled={false} onChange={() => {}} />)
    expect(html).toContain(hookEvents[0].hint + (language === 'en' ? '. ' : '。'))
    expect(html).toContain(language === 'en' ? 'SessionStart/End match the source/end reason. Commands in the same group run in display order.' : 'SessionStart/End 匹配来源／结束原因。同组命令按显示顺序执行。')
    if (language === 'en') expect(html).not.toMatch(/[\u3400-\u9fff]/)
    expect(JSON.parse(saved).hooks.PreToolUse[0].hooks[0].command).toContain('The command deletes .git; check the intended operation.')
  }
})

it('formats complete interval schedules with singular and plural units after language switches', () => {
  const cases = [
    [60, 'Every 1 minute', '每 1 分钟'],
    [120, 'Every 2 minutes', '每 2 分钟'],
    [3540, 'Every 59 minutes', '每 59 分钟'],
    [3600, 'Every 1 hour', '每 1 小时'],
    [3660, 'Every 61 minutes', '每 61 分钟'],
    [7200, 'Every 2 hours', '每 2 小时'],
  ] as const
  for (const language of ['en','zh-CN','en'] as const) {
    setLocale(language)
    for (const [seconds, english, chinese] of cases) {
      expect(scheduleLabel({kind:'interval', seconds})).toBe(language === 'en' ? english : chinese)
    }
    expect(scheduleLabel({kind:'once',at:1800000000})).toBe(language === 'en' ? 'Only once' : '仅一次')
    expect(scheduleLabel({kind:'daily',time:'09:00'})).toBe(language === 'en' ? 'Every day at 09:00' : '每天 09:00')
  }
})

it('describes truncated member previews without promising complete final output',()=>{
  for(const locale of ['en','zh-CN','en'] as const){
    setLocale(locale)
    const html=renderToStaticMarkup(<AssistantBlock hideActions block={{kind:'assistant',id:'preview',at:1,text:'Original preview 原文',previewTruncated:true}} />)
    expect(html).toContain(locale==='en'?'Only recent text is previewed. Check the saved member result when available; it may also be truncated.':'这里只预览最近文本。结果可用后请查看已保存的成员结果；保存的结果也可能截断。')
    expect(html).toContain('Original preview 原文')
  }
})
