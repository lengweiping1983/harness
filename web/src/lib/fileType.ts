export type FileType = 'react' | 'typescript' | 'javascript' | 'python' | 'markdown' | 'config' | 'image' | 'style' | 'shell' | 'file'

export function fileType(path: string): FileType {
  const name = path.split(/[\\/]/).pop()?.toLowerCase() ?? ''
  const extension = name.split('.').pop() ?? ''
  if (['tsx', 'jsx'].includes(extension)) return 'react'
  if (['ts', 'mts', 'cts'].includes(extension)) return 'typescript'
  if (['js', 'mjs', 'cjs'].includes(extension)) return 'javascript'
  if (['py', 'pyi', 'pyw'].includes(extension)) return 'python'
  if (['md', 'mdx', 'markdown'].includes(extension)) return 'markdown'
  if (['json', 'jsonc', 'yaml', 'yml', 'toml', 'ini', 'xml'].includes(extension) || name === 'dockerfile' || name.startsWith('.env') || name === '.gitignore') return 'config'
  if (['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'ico', 'avif'].includes(extension)) return 'image'
  if (['css', 'scss', 'sass', 'less'].includes(extension)) return 'style'
  if (['sh', 'bash', 'zsh', 'ps1', 'bat', 'cmd'].includes(extension)) return 'shell'
  return 'file'
}
