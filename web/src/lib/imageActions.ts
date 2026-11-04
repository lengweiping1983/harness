import {ResourceError} from './resourceError'

type SavePickerWindow = Window & {
  showSaveFilePicker?: (options: { suggestedName: string }) => Promise<FileSystemFileHandle>
}

async function imageBlob(src: string): Promise<Blob> {
  const response = await fetch(src)
  if (!response.ok) throw new ResourceError({key: 'ui.imageActions.httpError', params: {status: response.status}})
  return response.blob()
}

async function clipboardPng(src: string): Promise<Blob> {
  const blob = await imageBlob(src)
  if (blob.type === 'image/png') return blob
  const bitmap = await createImageBitmap(blob)
  try {
    const canvas = document.createElement('canvas')
    canvas.width = bitmap.width
    canvas.height = bitmap.height
    const context = canvas.getContext('2d')
    if (!context) throw new ResourceError({key: 'ui.imageActions.canvasUnavailable'})
    context.drawImage(bitmap, 0, 0)
    return await new Promise<Blob>((resolve, reject) => canvas.toBlob(
      result => result ? resolve(result) : reject(new ResourceError({key: 'ui.imageActions.pngFailed'})), 'image/png',
    ))
  } finally { bitmap.close() }
}

export async function copyPng(blob: Blob | Promise<Blob>): Promise<void> {
  const image = Promise.resolve(blob)
  // 浏览器可能在读取图片 Promise 前拒绝授权，仍须观察已开始的编码结果。
  void image.catch(() => {})
  if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') throw new ResourceError({key: 'ui.imageActions.clipboardUnavailable'})
  try {await navigator.clipboard.write([new ClipboardItem({ 'image/png': image })])}
  catch (cause) {
    if (cause instanceof ResourceError) throw cause
    const denied = cause instanceof Error && ['NotAllowedError', 'SecurityError'].includes(cause.name)
    const desktop = typeof window !== 'undefined' && Boolean((window as Window & {harnessDesktop?: unknown}).harnessDesktop)
    const key = denied ? (desktop ? 'ui.imageActions.clipboardDeniedDesktop' : 'ui.imageActions.clipboardDeniedBrowser') : 'ui.imageActions.copyFailed'
    throw Object.assign(new ResourceError({key}), {cause})
  }
}

export async function copyImage(src: string): Promise<void> {
  if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') throw new ResourceError({key: 'ui.imageActions.clipboardUnavailable'})
  // 在点击时立即请求剪贴板，图片读取和格式转换由 Promise 完成。
  await copyPng(clipboardPng(src))
}

export async function saveImageAs(src: string, name: string): Promise<void> {
  const picker = (window as SavePickerWindow).showSaveFilePicker
  if (picker) {
    const handle = await picker.call(window, { suggestedName: name })
    const blob = await imageBlob(src)
    const destination = await handle.createWritable()
    try {
      await destination.write(blob)
      await destination.close()
    } catch (error) {
      await destination.abort().catch(() => {})
      throw error
    }
  } else {
    const url = URL.createObjectURL(await imageBlob(src))
    const link = document.createElement('a')
    link.href = url
    link.download = name
    document.body.append(link)
    link.click()
    link.remove()
    // 下载读取完成前保留地址，且不回收附件自己的预览地址。
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  }
}
