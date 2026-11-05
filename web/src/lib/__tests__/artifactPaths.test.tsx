import {afterEach, expect, it} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {setLocale, t} from '@/i18n'
import {FileChangesCard} from '@/components/chat/blocks/FileChangesCard'
import {DeliverablesCard} from '@/components/chat/blocks/DeliverablesCard'
import {FileChip} from '@/components/markdown/FileChip'
import {fileChangeRows} from '../fileChanges'
import {setFileRoot} from '../fileRoot'
import {relativizeToRoot, workspaceRelativePath} from '../deliverables'
import {resolveLink} from '../fileLinks'

afterEach(() => {setFileRoot(null); setLocale('zh-CN')})

it('converts project paths without turning outside absolute paths into project files', () => {
  for (const [path, root, expected] of [
    ['/project/images/bow.svg', '/project', 'images/bow.svg'],
    ['images/bow.svg', '/project', 'images/bow.svg'],
    ['/private/tmp/bow.svg', '/project', null],
    ['/project-other/bow.svg', '/project', null],
    ['/project/../other/bow.svg', '/project', null],
    ['../other/bow.svg', '/project', null],
    ['/project/bow.svg', null, null],
    ['/bow.svg', '/', 'bow.svg'],
    ['C:\\Project\\Images\\Bow.svg', 'c:\\project', 'Images/Bow.svg'],
    ['D:\\Temp\\Bow.svg', 'C:\\Project', null],
    ['//server/share/bow.svg', '//server/share', 'bow.svg'],
  ] as const) expect(workspaceRelativePath(path, root)).toBe(expected)
  expect(relativizeToRoot('/private/tmp/bow.svg', '/project')).toBe('/private/tmp/bow.svg')
})

it('rejects outside image links while retaining in-project image previews', () => {
  setFileRoot('/project')
  for (const path of ['/tmp/bow.svg', '/private/tmp/bow.svg', '/project-other/bow.svg', '/tmp/bow.svg#L3', '/tmp%2Fbow.svg']) {
    const link = resolveLink(path, true)
    expect(link.kind).toBe('unsafe')
    expect(link.workspacePath).toBeUndefined()
    expect(link.href).toBe('#')
  }
  expect(resolveLink('/project/images/bow.svg', true)).toMatchObject({kind:'image', workspacePath:'images/bow.svg', workspaceRoot:'/project'})
  setFileRoot('C:\\Project')
  expect(resolveLink('D:\\Temp\\Bow.svg').kind).toBe('unsafe')
  expect(resolveLink('c:\\project\\Images\\Bow.svg')).toMatchObject({kind:'image', workspacePath:'Images/Bow.svg'})
})

it('renders actual outside changes as their absolute paths with bilingual guidance and no false link', () => {
  setFileRoot('/project')
  const rows = fileChangeRows([{kind:'tool', id:'write', callId:'write', at:1, turn:1, name:'write', status:'ok',
    input:{path:'/tmp/bow.svg'}, display:{path:'/private/tmp/bow.svg', changed:true, diff:'@@ -0,0 +1 @@\n+<svg/>'}}], false)
  const changes = rows.find(row => row.kind === 'changes')!
  if (changes.kind !== 'changes') throw new Error('missing changes')
  expect(changes.files[0]!.path).toBe('/private/tmp/bow.svg')
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    const html = renderToStaticMarkup(<FileChangesCard id="changes" files={changes.files} sessionId="owner" turn={1} pending={false} />)
    expect(html).toContain('/private/tmp/bow.svg')
    expect(html).toContain(t('ui.files.outsideWorkspace'))
    expect(html).not.toContain('<a ')
    expect(html).not.toContain('/api/v1/file?')
  }
  expect(renderToStaticMarkup(<FileChip path="/project/images/bow.svg" />)).toContain('path=images%2Fbow.svg')
})

it('historical outside deliverables have no thumbnail or download and valid project files remain available', () => {
  setFileRoot('/different-project')
  for (const locale of ['en', 'zh-CN', 'en'] as const) {
    setLocale(locale)
    const block = {kind:'deliverables' as const, id:'delivery', at:1, workspaceRoot:'/project',
      files:[{path:'/private/tmp/bow.svg', name:'bow.svg', size_bytes:6}]}
    const outside = renderToStaticMarkup(<DeliverablesCard block={block} />)
    expect(outside).toContain('/private/tmp/bow.svg')
    expect(outside).toContain(t('ui.files.outsideWorkspace'))
    expect(outside).not.toContain('/api/v1/file?')
    expect(outside).not.toContain('<img')
    const inside = renderToStaticMarkup(<DeliverablesCard block={{...block, files:[{path:'/project/bow.svg', name:'bow.svg', size_bytes:6}]}} />)
    expect(inside).toContain('path=bow.svg')
    expect(inside).toContain('root=%2Fproject')
    expect(inside).toContain('download=1')
    expect(inside).not.toContain(t('ui.files.outsideWorkspace'))
  }
})
