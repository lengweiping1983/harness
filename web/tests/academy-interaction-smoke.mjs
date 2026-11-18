import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {browserScenario} from './browser-scenario.mjs'

const entry = `
import React from 'react'
import {createRoot} from 'react-dom/client'
import {AcademyStepCard} from '/src/components/inspector/AcademySteps.tsx'
import {AcademyWorldCard} from '/src/components/inspector/AcademyWorld.tsx'
import {AcademyStudio} from '/src/components/inspector/AcademyStudio.tsx'
import {AcademyReviewCard} from '/src/components/inspector/AcademyMastery.tsx'
import {academyApi} from '/src/lib/academy.ts'
import {setLocale, useLocale} from '/src/i18n/index.ts'
import '/src/styles.css'
window.locale=setLocale;window.shaded=null;window.asked=[];window.reviewed=null
const ordering={id:'ord',kind:'ordering',prompt:'Order the steps.',feedback:'Think order.',items:[{id:'a',label:'Alpha'},{id:'b',label:'Beta'},{id:'c',label:'Gamma'}]}
const numeric={id:'num',kind:'numeric',prompt:'Pick on the line.',feedback:'Think.',tolerance:0,number_line:{min:0,max:12}}
const matching={id:'mat',kind:'matching',prompt:'Pair them.',feedback:'Match.',left:[{id:'l1',label:'Left one'},{id:'l2',label:'Left two'}],right:[{id:'r1',label:'Right one'},{id:'r2',label:'Right two'}]}
const lesson={id:'l1',mode:'study',order:1,title:'Lesson',goal:'Goal',reading:'Reading',milestone:false,run_command:[],source_manifest:[],references:[],prerequisites:[],trace_labels:{},knowledge_checks:[]}
const course={id:'c1',version:'1',origin:'provided',source_revision:'f',title:'Course',description:'',references:[],lessons:[lesson]}
const project={id:'p1',course_id:'c1',course_version:'1',name:'Lab',revision:1,active_lesson_id:'l1',course_fingerprint:'a'.repeat(64),created_at:1,progress:[],root:'/x',fingerprint:'a'.repeat(64),sandbox:'unavailable'}
const liveProps={course,project:{...project,id:'p2'},lesson,active:false,disabled:false,tutorTurns:[],struggleRevealed:false,checkAnswers:new Map(),note:'',studyReady:false,studyCompleted:false,onClose:()=>{},onOpenLesson:()=>{},onAskCoach:()=>{},onStrugglePhase:()=>{},onCheckAnswer:()=>{},onNote:()=>{},onComplete:()=>{},onRefresh:()=>{}}
academyApi.mastery=async()=>({ok:true,data:{mastery:{skills:[{lesson_id:'l1',title:'Lesson',stage:'',state:'learning',stability_days:2,difficulty:5,retrievability:0.62,due_at:null,last_evidence_at:1,evidence_count:3,reviews:{attempts:1,correct:1,delayed_correct:0}}],due_count:0,reviews:{attempts:1,correct:1,delayed_correct:0},engagement:{constructive:1,active:1,total:2,constructive_ratio:0.5}}}})
const reviewItem={lesson_id:'l1',title:'Lesson',goal:'Goal',stage:'',retrievability:0.8,overdue_days:1,checks:[{id:'q1',prompt:'Which is larger?',options:[{id:'right',label:'One half'},{id:'wrong',label:'One third'}]},{id:'q2',prompt:'Which is smaller?',options:[{id:'a',label:'One third'},{id:'b',label:'One half'}]}]}
const haloProps={...liveProps,project:{...project,id:'p3'},active:true}
function App(){useLocale();const [coachTurns,setCoachTurns]=React.useState([]);window.setCoachTurns=setCoachTurns;return React.createElement('div',{className:'space-y-6 p-4'},
 React.createElement('div',{id:'order-card'},React.createElement(AcademyStepCard,{step:ordering,busy:false,feedback:null,onSubmit:()=>{},onNext:()=>{}})),
 React.createElement('div',{id:'hint-card'},React.createElement(AcademyStepCard,{step:numeric,busy:false,feedback:{correct:false,text:'Not yet.',hints:['First hint','Second hint','Third hint']},onSubmit:()=>{},onNext:()=>{}})),
 React.createElement('div',{id:'num-card'},React.createElement(AcademyStepCard,{step:numeric,busy:false,feedback:null,onSubmit:()=>{},onNext:()=>{}})),
 React.createElement('div',{id:'match-card'},React.createElement(AcademyStepCard,{step:matching,busy:false,feedback:null,onSubmit:()=>{},onNext:()=>{}})),
 React.createElement('div',{id:'world-card'},React.createElement(AcademyWorldCard,{kind:'fraction-bar',session:{id:'w1',project_id:'p1',lesson_id:'l1',request_id:'r1',kind:'fraction-bar',params:{parts:4,shaded:1},challenge:{prompt:'Shade it.',options:[],target:{}},status:'observing',observations:[{params:{parts:4,shaded:1},state:{parts:4,shaded:1,label:'1/4'},at:1}],note:'',created_at:1,completed_at:null},params:{parts:4,shaded:1},note:'',busy:false,onParams:p=>{window.shaded=p.shaded},onNote:()=>{},onStart:()=>{},onPredict:()=>{},onObserve:()=>{},onComplete:()=>{}})),
 React.createElement('div',{id:'studio-live',style:{height:600,position:'relative',transform:'translateZ(0)'}},React.createElement(AcademyStudio,liveProps)),
 React.createElement('div',{id:'review-card'},React.createElement(AcademyReviewCard,{item:reviewItem,busy:false,onSubmit:a=>{window.reviewed=a}})),
 React.createElement('div',{id:'studio-halo',style:{height:600,position:'relative',transform:'translateZ(0)'}},React.createElement(AcademyStudio,haloProps)),
 React.createElement('div',{id:'studio-coach',style:{height:600,position:'relative',transform:'translateZ(0)'}},React.createElement(AcademyStudio,{...liveProps,project:{...project,id:'p4'},tutorTurns:coachTurns})),
 React.createElement('div',{id:'studio-done',style:{height:600,position:'relative',transform:'translateZ(0)'}},React.createElement(AcademyStudio,{course,project,lesson,active:false,disabled:false,tutorTurns:[],struggleRevealed:false,checkAnswers:new Map(),note:'done',studyReady:true,studyCompleted:true,onClose:()=>{},onOpenLesson:()=>{},onAskCoach:p=>{window.asked.push(p)},onStrugglePhase:()=>{},onCheckAnswer:()=>{},onNote:()=>{},onComplete:()=>{},onRefresh:()=>{}})))}
createRoot(document.getElementById('root')).render(React.createElement(App))
`
const html='<html><body><div id="root"></div><script type="module" src="/__academy-interaction-entry.js"></script></body></html>'
await browserScenario(async({server,browser}) => {
  for (const theme of ['light','dark']) for (const width of [390,1440]) {
    const page=await browser.newPage({viewport:{width,height:1000}})
    const errors=[]
    page.on('pageerror',error=>errors.push(error.message))
    await page.goto(server.resolvedUrls.local[0]+'__academy-interaction')
    await page.waitForFunction(()=>typeof window.locale==='function')
    await page.evaluate(theme=>document.documentElement.className=theme,theme)
    await page.evaluate(()=>window.locale('en'))
    // 完成庆祝＋进度环（挂载即检查，彩带短暂）。
    await page.locator('#studio-done [data-progress-ring]').waitFor()
    await page.locator('#studio-done [data-confetti]').waitFor()
    // 拖拽排序：把第一张拖到第三张位置，顺序改变。
    const list=page.locator('#order-card [data-ordering-list]')
    const before=await list.locator('li span.min-w-0').allTextContents()
    await list.locator('[data-order-index="0"]').dragTo(list.locator('[data-order-index="2"]'))
    const after=await list.locator('li span.min-w-0').allTextContents()
    assert.notDeepEqual(after,before,'drag must reorder')
    assert.equal(after[2],'Alpha','dragged item lands at target')
    await page.locator('#order-card [data-order-undo]').click()
    assert.deepEqual(await list.locator('li span.min-w-0').allTextContents(),before,'undo restores original order')
    await page.locator('#order-card [data-order-redo]').click()
    assert.equal((await list.locator('li span.min-w-0').allTextContents())[2],'Alpha','redo reapplies reorder')
    // 点选配对：先左后右，左槽显示右标签。
    const board=page.locator('#match-card [data-matching-board]')
    await board.locator('[data-match-left="l1"]').click()
    await board.locator('[data-match-right="r1"]').click()
    await board.locator('[data-match-left="l1"]').getByText('Right one',{exact:true}).waitFor()
    assert.equal(await board.locator('[data-match-right="r1"]').isDisabled(),true,'assigned right disabled')
    // 渐进式提示：初始只揭示第一条，点击揭示下一条。
    const hintBox=page.locator('#hint-card [data-progressive-hints]')
    await hintBox.getByText('First hint',{exact:true}).waitFor()
    assert.equal(await hintBox.getByText('Second hint',{exact:true}).count(),0,'second hint hidden initially')
    await page.locator('#hint-card [data-next-hint]').click()
    await hintBox.getByText('Second hint',{exact:true}).waitFor()
    // 可探索数轴：点/拖数轴点直接作答。
    const slider=page.locator('#num-card [role="slider"]')
    const box=await slider.boundingBox()
    await slider.click({position:{x:box.width*0.75,y:box.height/2}})
    assert.equal(await page.locator('#num-card input[type="number"]').inputValue(),'9','number line drag sets answer')
    // 可探索分数条：点/拖涂色格直接改 shaded。
    await page.locator('#world-card [data-shade-index="2"]').click()
    assert.equal(await page.evaluate(()=>window.shaded),3,'click shade sets shaded=3')
    // 键盘-first：方向键切换环节。
    await page.locator('#studio-done [data-studio-phases] button').last().click()
    await page.keyboard.press('ArrowLeft')
    await page.locator('#studio-done [data-studio-phase="note"]').waitFor()
    assert.equal(await page.evaluate(()=>document.activeElement?.tagName),'TEXTAREA','phase change auto-focuses primary input')
    // 会话连续性：live 舞台切到 note 后重载仍停留 note（按项目+课次持久化）。
    await page.locator('#studio-live [data-studio-phases] button').nth(1).click()
    await page.locator('#studio-live [data-studio-phase="note"]').waitFor()
    const phaseStorageKey='harness.academy.phase.v1:'+JSON.stringify(['p2','l1'])
    assert.equal(await page.evaluate(key=>localStorage.getItem(key),phaseStorageKey),'note','phase persisted per project+lesson')
    await page.reload()
    await page.waitForFunction(()=>typeof window.locale==='function')
    await page.locator('#studio-live [data-studio-phase="note"]').waitFor()
    assert.equal(await page.evaluate(key=>localStorage.getItem(key),phaseStorageKey),'note','saved phase survives reload')
    // 重测卡片流：一次一题，键盘 1-9 选项、Enter 继续/提交，进度点可回看且保留选择。
    const review=page.locator('#review-card [data-review-card]')
    await review.getByText('Which is larger?',{exact:true}).waitFor()
    assert.equal(await review.getByText('Which is smaller?',{exact:true}).count(),0,'one question at a time')
    assert.equal(await review.locator('[data-review-dot]').count(),2,'progress dot per question')
    assert.equal(await review.locator('button[type="submit"]').isDisabled(),true,'submit disabled until all answered')
    await review.locator('input[type="radio"]').first().focus()
    await page.keyboard.press('2')
    assert.equal(await review.locator('input[type="radio"]').nth(1).isChecked(),true,'number key selects option')
    assert.equal(await review.locator('button[type="submit"]').isDisabled(),true,'submit gated on confidence')
    await page.keyboard.press('Enter')
    await page.waitForTimeout(150)
    assert.equal(await review.getByText('Which is smaller?',{exact:true}).count(),0,'advance gated on confidence')
    await review.locator('[data-confidence="sure"]').click()
    await page.waitForFunction(()=>document.activeElement?.hasAttribute('data-review-continue'))
    await page.keyboard.press('Enter')
    await review.getByText('Which is smaller?',{exact:true}).waitFor()
    assert.equal(await page.evaluate(()=>document.activeElement?.type),'radio','advance focuses next question option')
    await review.locator('[data-review-dot="0"]').click()
    await review.getByText('Which is larger?',{exact:true}).waitFor()
    assert.equal(await review.locator('input[type="radio"]').nth(1).isChecked(),true,'selection preserved across navigation')
    await review.locator('[data-review-dot="1"]').click()
    await review.getByText('Which is smaller?',{exact:true}).waitFor()
    await page.keyboard.press('1')
    await review.locator('[data-confidence="guess"]').click()
    await page.waitForFunction(()=>document.activeElement?.type==='submit')
    await page.keyboard.press('Enter')
    assert.deepEqual(await page.evaluate(()=>window.reviewed),[{check_id:'q1',option_id:'wrong',confidence:'sure'},{check_id:'q2',option_id:'a',confidence:'guess'}],'answers submitted in course order with confidence')
    // 记忆光环：旅程节点以 FSRS 可提取性渲染记忆保持环。
    await page.locator('#studio-halo [data-journey-memory]').waitFor()
    assert.equal(await page.locator('#studio-halo [data-journey-memory]').getAttribute('aria-label'),'Memory retention 62%','halo shows retrievability')
    // 教练对话流：舞台内流式陪伴，partial 实时更新，问题收尾触发回应邀请。
    const coachTurnBase={project_id:'p4',lesson_id:'l1',request_id:'r',input_fingerprint:'a'.repeat(64),allow_execution:false,require_execution:false,model:'m',language:'en',actions:[],created_at:1}
    const coachThread=page.locator('#studio-coach main [data-studio-coach-thread]')
    await coachThread.getByText('your learning coach',{exact:false}).waitFor()
    assert.equal(await page.locator('#studio-coach footer [data-studio-coach-thread]').count(),0,'conversation lives in the stage, not the footer')
    await page.evaluate(base=>window.setCoachTurns([{...base,id:'run',status:'running',prompt:'ask\nWhy equal parts?',response:'',partial_response:'First think about equal parts'}]),coachTurnBase)
    await coachThread.getByText('First think about equal parts',{exact:false}).waitFor()
    assert.equal(await coachThread.locator('[aria-busy="true"]').count(),1,'partial stream is busy')
    assert.equal(await coachThread.locator('.streaming-caret').count(),1,'streaming caret present')
    await page.evaluate(base=>window.setCoachTurns([{...base,id:'run',status:'running',prompt:'ask\nWhy equal parts?',response:'',partial_response:'First think about equal parts, then compare'}]),coachTurnBase)
    await coachThread.getByText('First think about equal parts, then compare',{exact:false}).waitFor()
    await page.evaluate(base=>window.setCoachTurns([{...base,id:'run',status:'completed',prompt:'ask\nWhy equal parts?',response:'Because halves share one name. Why do you think so?'}]),coachTurnBase)
    await page.locator('#studio-coach [data-coach-awaiting]').waitFor()
    await page.locator('#studio-coach [data-coach-awaiting]').click()
    assert.equal(await page.evaluate(()=>document.activeElement?.tagName),'TEXTAREA','reply invitation focuses composer')
    // 专注模式：收敛旅程/会话条为环节圆点，Ctrl+. 切换，偏好重载后保留。
    await page.locator('#studio-live [data-academy-pref-zen]').click()
    await page.waitForFunction(()=>!document.querySelector('#studio-live [data-studio-journey]'))
    assert.equal(await page.locator('#studio-live [data-studio-session]').count(),0,'zen hides session bar')
    assert.equal(await page.locator('#studio-live [data-phase-dot]').count(),3,'zen renders phase dots')
    assert.equal(await page.locator('#studio-live [data-academy-pref-font]').count(),0,'zen hides font toggle')
    await page.locator('#studio-live [data-phase-dot="reading"]').click()
    await page.locator('#studio-live [data-studio-phase="reading"]').waitFor()
    await page.keyboard.press('Control+.')
    await page.locator('#studio-live [data-studio-journey]').waitFor()
    await page.locator('#studio-live [data-academy-pref-zen]').click()
    await page.reload()
    await page.waitForFunction(()=>typeof window.locale==='function')
    await page.locator('#studio-live [data-phase-dot]').first().waitFor()
    assert.equal(await page.locator('#studio-live [data-studio-journey]').count(),0,'zen preference survives reload')
    await page.locator('#studio-live [data-academy-pref-zen]').click()
    await page.locator('#studio-live [data-studio-journey]').waitFor()
    // 触摸滑动：水平滑动切换环节（与方向键等价），起点在控件上时不拦截。
    const swipe=async(selector,dx)=>{await page.evaluate(({selector,dx})=>{
      const target=document.querySelector(selector)
      const rect=target.getBoundingClientRect()
      const x=rect.left+rect.width/2,y=rect.top+rect.height/2
      const emit=(type,cx)=>{const touch=new Touch({identifier:1,target,clientX:cx,clientY:y});target.dispatchEvent(new TouchEvent(type,{bubbles:true,cancelable:true,touches:type==='touchend'?[]:[touch],changedTouches:[touch]}))}
      emit('touchstart',x);emit('touchend',x+dx)
    },{selector,dx})}
    await swipe('#studio-live main h3',-120)
    await page.locator('#studio-live [data-studio-phase="note"]').waitFor()
    await swipe('#studio-live main h3',120)
    await page.locator('#studio-live [data-studio-phase="reading"]').waitFor()
    await swipe('#studio-live [data-studio-phases] button',-120)
    await page.waitForTimeout(200)
    assert.equal(await page.locator('#studio-live [data-studio-phase]').getAttribute('data-studio-phase'),'reading','swipe from a control does not navigate')
    // 记忆光环：旅程节点以 FSRS 可提取性渲染记忆保持环。
    // 对话式快捷追问：点击 chip 以对应自主档发送教练请求。
    await page.locator('#studio-done [data-coach-suggest="hint"]').click()
    assert.equal(await page.evaluate(()=>window.asked.length),1,'suggestion chip asks coach')
    assert.ok((await page.evaluate(()=>window.asked[0])).includes('Lesson'),'prompt carries lesson context')
    // 语言矩阵：交互文案随语言解析。
    for (const language of ['en','zh-CN','en']) {
      const copy=JSON.parse(await readFile(new URL('../src/i18n/locales/'+language+'.json',import.meta.url),'utf-8'))
      await page.evaluate(language=>window.locale(language),language)
      await page.locator('#match-card [data-match-left="l2"]').getByText(copy['ui.academy.steps.matchEmpty'],{exact:true}).waitFor()
      const bounds=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}))
      assert.ok(bounds.scroll<=bounds.width,[theme,width,language].join('/'))
    }
    assert.deepEqual(errors,[])
    await page.close()
  }
  console.log('Academy interaction: drag reorder, click-to-pair, confetti, progress ring, session continuity, review card flow, zen mode, swipe, memory halo, streaming coach conversation, language/theme/width matrix verified')
},{entry:'/__academy-interaction-entry.js',vite:{plugins:[{
  name:'academy-interaction-fixture',resolveId(id){if(id==='/__academy-interaction-entry.js')return id},load(id){if(id==='/__academy-interaction-entry.js')return entry},
  configureServer(server){server.middlewares.use('/__academy-interaction',async(_req,res,next)=>{try{res.setHeader('Content-Type','text/html');res.end(await server.transformIndexHtml('/__academy-interaction',html))}catch(error){next(error)}})},
}]}})
