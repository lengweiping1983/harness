import {expect,it} from 'vitest'
import {fileIcon} from '@/lib/fileIcon'

it('recognizes extensionless names declared by the icon resource',()=>{
  for(const name of ['AUTHORS','CHANGELOG','CONTRIBUTORS','LICENSE']) {
    for(const path of [name,`/project/${name.toLowerCase()}`,`C:\\project\\${name}`]) {
      expect(fileIcon(path).token).toBe('text')
    }
  }
})

it('preserves name priority, longest suffixes and unknown-file fallback',()=>{
  for(const [path,token] of [
    ['README.md','markdown'],['.BASHRC','bash'],['vite.config.ts','vite'],
    ['source.mdx.tsx','markdown'],['component.TSX','react'],['.env.local','text'],
    ['archive.tar.gz','zip'],['unknown','default'],['file.unknown','default'],
    ['js','default'],['constructor','default'],['file.constructor','default'],
  ]) expect(fileIcon(path).token).toBe(token)
})
