import assert from 'node:assert/strict'

/** 只依据真实项目回执分类环境限制，缺失或损坏的协议仍属于业务失败。 */
export function requireAcademySandbox(project) {
  assert.ok(['seatbelt', 'bubblewrap', 'unavailable'].includes(project?.sandbox), 'Academy project must report its current sandbox backend')
  if (project.sandbox !== 'unavailable') return
  const error = Error('Academy execution requires an available OS sandbox (Linux bubblewrap or macOS seatbelt); this environment is not verified')
  error.browserInfrastructure = true
  throw error
}
