import {describe, expect, it} from 'vitest'
import {sessionTitle, sessionTaskTitle} from '@/lib/sessionTitle'
import type {SessionSummary} from '@/types'

const summary = (text: string, title?: string) => ({summary: text, title}) as SessionSummary

describe('会话名称中的输入模式命令', () => {
  it('已物化空会话的计划和目标摘要只展示正文', () => {
    expect(sessionTitle(summary('/plan 梳理仓库架构'))).toBe('梳理仓库架构')
    expect(sessionTitle(summary('/goal 完成目标验收'))).toBe('完成目标验收')
    expect(sessionTitle(summary(' \n /plan\t检查\n边界  '))).toBe('检查 边界')
  })

  it('自动命名共用正文提取，保留普通文本和其他显式命令', () => {
    expect(sessionTaskTitle(' \n/goal\t完成  验收 ')).toBe('完成 验收')
    expect(sessionTaskTitle('/plan')).toBe('')
    expect(sessionTaskTitle('/goal \n')).toBe('')
    expect(sessionTaskTitle('修复 /plan 输入')).toBe('修复 /plan 输入')
    expect(sessionTaskTitle('/planning 检查')).toBe('/planning 检查')
    expect(sessionTaskTitle('/agent reviewer 检查')).toBe('/agent reviewer 检查')
  })

  it('不改写手动标题，裸模式摘要使用原来的空名称提示', () => {
    expect(sessionTitle(summary('/goal 正文', ' /plan 我的手动名称 '))).toBe('/plan 我的手动名称')
    expect(sessionTitle(summary('/plan'))).toBe(sessionTitle(null))
    expect(sessionTitle(summary('/goal'))).toBe(sessionTitle(null))
  })

  it('完整保留50个Unicode字符的标题，顶部和侧栏共用原文', () => {
    const title = '开发'.repeat(24) + '😀游'
    expect(Array.from(title)).toHaveLength(50)
    expect(sessionTitle(summary('Old summary', title))).toBe(title)
    expect(sessionTitle(summary('Different summary', title))).toBe(title)
  })
})
