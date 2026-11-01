import {describe, expect, it} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {ToolBlock} from '@/components/chat/blocks/ToolBlock'
import type {ToolBlockState} from '@/types'

const block = (display: unknown, status: ToolBlockState['status'] = 'ok'): ToolBlockState => ({
  kind: 'tool', id: 'command', callId: 'command', at: 0, name: 'bash', status,
  input: {command: 'python check.py'}, output: 'exit_code: 0', display,
})

describe('折叠命令的真实执行状态', () => {
  it.each([{exit_code: 2}, {exit_code: -9}, {timed_out: true}, {execution_state: 'not-started'}])('失败回执显示警告图标和错误颜色：%j', display => {
    const html = renderToStaticMarkup(<ToolBlock block={block(display)} />)
    expect(html).toContain('text-danger')
    expect(html).toContain('lucide-triangle-alert')
    expect(html).not.toContain('data-tool-icon="command"')
    expect(html).not.toContain('id="tool-detail-command"')
  })

  it.each([{exit_code: 0}, {}, {background: true, exit_code: 2}, {interrupted: true, exit_code: -9}, {not_executed: true}, {steering_deferred: true}])('成功、未知与未执行状态不误报命令失败：%j', display => {
    const html = renderToStaticMarkup(<ToolBlock block={block(display)} />)
    expect(html).not.toContain('text-danger')
    expect(html).not.toContain('lucide-triangle-alert')
  })
})
