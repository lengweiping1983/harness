import {describe, expect, it} from 'vitest'
import {parseMemoryEvidence, updateMemoryEvidenceNote} from '../memoryEvidence'

const files = 'harness:file-sources:v1\n' + JSON.stringify({note: '原说明', files: [{path: 'src/队列.py', version: '0123456789abcdef'}]})
const consolidated = 'harness:consolidation:v1\n' + JSON.stringify({request: 'a'.repeat(64), note: '整理说明', inputs: [
  {id: 'first', input_revision: 1, revision: 2, pinned: true},
  {id: 'second', input_revision: 3, revision: 3, pinned: false},
]})

describe('记忆证据说明编辑', () => {
  it.each([files, consolidated])('编辑说明保留机器引用，未编辑保留原始字节', raw => {
    const parsed = parseMemoryEvidence(raw)!
    expect(parsed).not.toBeNull()
    expect(updateMemoryEvidenceNote(raw, parsed.note)).toBe(raw)
    const prefixEnd = raw.indexOf('\n') + 1
    const changed = updateMemoryEvidenceNote(raw, '新的说明 <中文>')
    expect(JSON.parse(changed.slice(prefixEnd))).toEqual({...JSON.parse(raw.slice(prefixEnd)), note: '新的说明 <中文>'})
  })
  it('未知、损坏、超限和修订不一致保留原始编辑方式', () => {
    for (const raw of ['普通证据', files + '!', files.replace('v1', 'v2'), files.replace('0123456789abcdef', 'bad'),
      consolidated.replace('"revision":2', '"revision":5'), consolidated.replace('"note":', '"extra":0,"note":'),
      'harness:file-sources:v1\n' + ' '.repeat(1000)]) expect(parseMemoryEvidence(raw)).toBeNull()
    expect(updateMemoryEvidenceNote('普通证据', '修改后的普通证据')).toBe('修改后的普通证据')
  })
  it('编辑暂时超出存储预算后仍可缩短说明而不丢引用', () => {
    const large = updateMemoryEvidenceNote(files, '说明'.repeat(500))
    expect(large.length).toBeGreaterThan(1000)
    expect(parseMemoryEvidence(large)?.kind).toBe('files')
    const shortened = updateMemoryEvidenceNote(large, '缩短说明')
    expect(parseMemoryEvidence(shortened)).toEqual({...parseMemoryEvidence(files), note: '缩短说明'})
  })
})
