import {describe, expect, it} from 'vitest'
import {artifactPreviewTarget} from '../turnDelivery'

describe('original artifact preview location', () => {
  for (const [root, path, relative] of [
    ['/original', '/original/out/报告.md', 'out/报告.md'],
    ['D:\\Original', 'd:/original/Out/Report.md', 'Out/Report.md'],
    ['D:\\', 'd:\\Out\\Report.md', 'Out/Report.md'],
    ['\\\\Server\\Share', '\\\\server\\share\\Out\\Report.md', 'Out/Report.md'],
    ['/', '/Out/Report.md', 'Out/Report.md'],
  ]) it(`keeps the declared root ${root}`, () => {
    expect(artifactPreviewTarget({path: path!, workspace_root: root!, source_seq: 1})).toEqual({root, path: relative})
  })
  for (const [root, path] of [
    [undefined, '/original/report.md'], [null, '/original/report.md'], ['', '/original/report.md'],
    ['/Original', '/original/report.md'], ['/work', '/workspace/report.md'],
    ['/work', '/work/../outside/report.md'], ['/work/..', '/work/../outside/report.md'],
    ['/work', 'report.md'], ['/work', '/work'], ['relative', 'relative/report.md'],
  ] as const) it(`does not infer a directory for ${String(root)} / ${path}`, () => {
    expect(artifactPreviewTarget({path, workspace_root: root, source_seq: 1})).toBeNull()
  })
})
