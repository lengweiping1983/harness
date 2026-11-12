import {afterEach, describe, expect, it} from 'vitest'
import {setLocale} from '@/i18n'
import {commandFailed, commandResult, terminalWaitNote} from '../commandResults'
import type {ToolBlockState} from '@/types'
const block = (name: string, display: unknown, status: ToolBlockState['status'] = 'ok'): ToolBlockState => ({id:'tool',kind:'tool',at:0,turn:1,callId:'call',name,status,display,output:'Original output 原文'})
afterEach(() => setLocale('zh-CN'))
const reasons = ['stdin_read','inferred_idle','timeout','session_exit','cancelled',undefined] as const
const english = ['Prompt polling has ended.','Output is temporarily silent.','This wait timed out, but the program may still be running.','The terminal session has exited.','The terminal wait was interrupted, and the current program state must be verified.','Terminal input waiting has ended.']
const chinese = ['提示符判断结束等待。','输出暂时静默。','本次等待已超时，程序可能仍在运行。','终端已退出。','终端等待已中断，当前程序状态需要核实。','终端输入等待已结束。']
describe('structured command evidence and terminal wait copy', () => {
 it('renders complete wait explanations without turning waiting into command completion', () => {
  for (const locale of ['en','zh-CN','en'] as const) {
   setLocale(locale)
   for (const [index,reason] of reasons.entries()) {
    const expected = locale === 'en' ? english[index] + ' Waiting for completion does not mean the command finished and does not provide a per-command exit code.' : chinese[index] + '等待结束不代表命令完成，也不提供单条命令退出码。'
    const receipt = block('terminal_send',{terminal_wait:{reason},exit_code:7})
    expect(terminalWaitNote(reason)).toBe(expected)
    expect(commandResult(receipt)).toMatchObject({note:expected,exitCode:null})
    expect(commandFailed(receipt)).toBe(false)
    expect(receipt.output).toBe('Original output 原文')
   }
  }
 })
 it('treats inherited names and non-string reasons as unknown without invoking coercion', () => {
  for (const locale of ['en','zh-CN','en'] as const) {
   setLocale(locale)
   const expected = terminalWaitNote(undefined)
   for (const reason of ['toString','__proto__','constructor',null,42,{toString(){throw Error('Must not stringify protocol data')}}]) expect(terminalWaitNote(reason)).toBe(expected)
  }
 })
 it('keeps foreground, background and not-executed evidence distinct', () => {
  expect(commandFailed(block('bash',{exit_code:7}))).toBe(true)
  expect(commandResult(block('bash',{exit_code:7})).exitCode).toBe(7)
  for (const display of [{background:true,exit_code:7},{not_executed:true,exit_code:7},{steering_deferred:true,exit_code:7},{interrupted:true,exit_code:7}]) {
   expect(commandFailed(block('bash',display))).toBe(false)
   expect(commandResult(block('bash',display)).exitCode).toBe(null)
  }
  expect(commandResult(block('bash',{exit_code:7},'running')).exitCode).toBe(null)
  expect(commandFailed(block('read_file',{exit_code:7}))).toBe(false)
 })
 it('keeps successful process exit separate from incomplete output', () => {
  for (const locale of ['en','zh-CN','en'] as const) {
   setLocale(locale)
   const receipt = block('bash',{execution_state:'exited',exit_code:0,encoding_errors:[{channel:'stdout',encoding:'utf-8',offset:79,reason:'invalid start byte'}]},'error')
   expect(commandResult(receipt)).toMatchObject({label:locale==='en'?'Command exited; output is incomplete':'命令已退出，输出不完整',exitCode:0})
  }
 })

})
