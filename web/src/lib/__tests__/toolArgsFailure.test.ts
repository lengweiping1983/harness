import { expect, it } from 'vitest'
import { setLocale } from '@/i18n'
import { toolArgsFailure, toolArgsNote, fileWriteRejected } from '../toolArgsFailure'
import { toolActivity } from '../toolActivity'
import { commandFailed, commandResult } from '../commandResults'
import type { ToolBlockState } from '@/types'

const block: ToolBlockState = { kind:'tool',id:'x',callId:'x',at:0,name:'bash',status:'error',input:{timeout:120000},display:{code:'tool/args',details:{not_executed:true}} }
it('distinguishes parameter rejection from command execution failure in every language', () => {
  for (const locale of ['en','zh-CN','en'] as const) {
    setLocale(locale)
    expect(toolArgsFailure(block)).toBe(true)
    expect(commandFailed(block)).toBe(false)
    expect(commandResult(block).exitCode).toBeNull()
    expect(toolActivity(block).label).toBe(commandResult(block).label)
    expect(toolArgsNote(block)).toContain('120000')
    expect(toolArgsNote({...block,input:{timeout:120,wrong:true}})).not.toContain('120000')
  }
})
it('does not infer nonexecution from error text or arbitrary failures', () => {
  expect(toolArgsFailure({...block,display:{code:'tool/args'},output:'not_executed'})).toBe(false)
  expect(toolArgsFailure({...block,status:'ok'})).toBe(false)
  expect(commandFailed({...block,display:{exit_code:7}})).toBe(true)
  expect(fileWriteRejected({...block,name:'write',display:{code:'FS_EXISTS'}})).toBe(true)
  expect(fileWriteRejected({...block,name:'write',display:{code:'FS_STALE'}})).toBe(false)
  expect(fileWriteRejected({...block,display:{code:'FS_EXISTS'}})).toBe(false)
})
