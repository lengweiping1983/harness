import type {ResourceCopy, ResourceText} from '@/types'
/**
 * 附件草稿轨的领域逻辑（纯函数，零 DOM）。
 *
 * 为什么单独抽一层：拖放 / 粘贴 / 选择器三个入口会各自判断一遍
 * "这个文件能不能收"，一旦三处判断漂移，用户就会遇到"拖进来不行、
 * 粘贴却可以"这种最伤信任的 bug。把规则收在这里，三个入口共用一份实现，
 * 测试也能直接覆盖规则本身而不必模拟文件选择器。
 *
 * 规则与后端 `harness/web/attachments.py` 一一对应：**客户端只为体验，
 * 后端才是裁决者**。客户端提前拒绝只是为了省一次往返和给即时反馈，
 * 真正的准入永远在服务端重做一遍。
 */

/** 单文件上限（与后端 MAX_ATTACHMENT_BYTES 一致）。 */
export const MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024

/** 单批合计上限（与后端 MAX_BATCH_BYTES 一致）。 */
export const MAX_BATCH_BYTES = 96 * 1024 * 1024

/** 单批文件数上限（与后端 MAX_BATCH_FILES 一致）。 */
export const MAX_BATCH_FILES = 20

/**
 * 图片媒体类型 -> 可接受的扩展名集合。
 *
 * 存在这张表不是为了"认得出是图片"，而是为了**校验扩展名与字节一致**：
 * 字节是 PNG 而名字叫 `.gif`，若放行，界面按 gif 渲染、服务端按 png 存储，
 * 元数据就自相矛盾了。
 */
const IMAGE_EXTENSIONS: Record<string, Set<string>> = {
  'image/png': new Set(['png']),
  'image/jpeg': new Set(['jpg', 'jpeg']),
  'image/gif': new Set(['gif']),
  'image/bmp': new Set(['bmp']),
  'image/webp': new Set(['webp']),
}

/**
 * 允许的非图片扩展名。
 *
 * 白名单而非黑名单：附件要能被模型读取，可执行文件与脚本没有任何读取价值。
 */
const ALLOWED_NON_IMAGE = new Set([
  'txt', 'log', 'md', 'markdown', 'csv', 'json', 'jsonl',
  'pdf', 'xml', 'yaml', 'yml',
])

/** 危险扩展名（与后端一致，双保险）。 */
const DENIED_EXTENSIONS = new Set([
  'exe', 'dll', 'com', 'bat', 'cmd', 'scr', 'pif', 'msi', 'msp',
  'vbs', 'vbe', 'js', 'jse', 'wsf', 'wsh', 'ps1', 'psm1',
  'jar', 'sh', 'bash', 'zsh', 'csh', 'ksh',
  'py', 'pyc', 'pyo', 'pyw', 'rb', 'pl', 'php',
  'app', 'deb', 'rpm', 'dmg', 'pkg', 'apk',
  'lnk', 'scf', 'reg', 'hta', 'cpl', 'inf', 'url',
])

/** 草稿轨里一个待发送附件的本地状态。 */
export interface DraftAttachment {
  /** 本地唯一 id（用于 key / 移除），与服务端 attachmentId 无关。 */
  localId: string
  name: string
  mediaType: string
  size: number
  isImage: boolean
  /** 尚未上传；上传完成后替换为服务端 id。 */
  attachmentId?: string
  /** 图片缩略图的 object URL；非图片为 undefined。 */
  previewUrl?: string
  /** 上传中 / 失败原因；成功上传后为 undefined。 */
  status?: 'pending' | 'uploading' | 'error'
  error?: ResourceText
}

/** 单个文件的准入结果。 */
export interface AdmissionResult {
  ok: boolean
  /** 拒绝时的机器码，与后端 AttachmentRejected.code 对齐。 */
  code?: string
  /** 拒绝提示保留显式资源身份，由显示处解析语言。 */
  message?: ResourceCopy
  /** 通过时的媒体类型。 */
  mediaType?: string
  isImage?: boolean
}

/** 取小写扩展名（不含点）。以点开头的整名（.gitignore）不是扩展名。 */
export function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.')
  if (dot <= 0 || dot < Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\'))) return ''
  return name.slice(dot + 1).toLowerCase()
}

/** 只探测头部若干字节足够判断全部魔数；省掉整文件读入。 */
const SNIFF_BYTES = 32

/**
 * 从字节判定媒体类型，**不信**文件自报的 `type`。
 *
 * 浏览器对未知类型一律填 `application/octet-stream`，拖进来的文件更没有
 * 类型可言 —— 只认魔数。WebP 特殊处理：RIFF 只占 4 字节，WAV/AVI 也是 RIFF，
 * 必须再看 `data[8:12] == "WEBP"` 才敢认。
 */
export function sniffMediaType(bytes: Uint8Array, complete = true): string | null {
  const head = bytes.subarray(0, SNIFF_BYTES)
  if (head.length >= 8) {
    if (head[0] === 0x89 && head[1] === 0x50 && head[2] === 0x4e && head[3] === 0x47) {
      return 'image/png'
    }
    if (head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff) return 'image/jpeg'
  }
  if (head.length >= 6) {
    if (head[0] === 0x47 && head[1] === 0x49 && head[2] === 0x46) return 'image/gif'
    if (head[0] === 0x42 && head[1] === 0x4d) return 'image/bmp'
  }
  if (head.length >= 12) {
    const riff =
      head[0] === 0x52 && head[1] === 0x49 && head[2] === 0x46 && head[3] === 0x46 &&
      head[8] === 0x57 && head[9] === 0x45 && head[10] === 0x42 && head[11] === 0x50
    if (riff) return 'image/webp'
  }
  if (head.length >= 5 && head[0] === 0x25 && head[1] === 0x50 && head[2] === 0x44 && head[3] === 0x46) {
    return 'application/pdf'
  }
  if (looksLikeText(bytes, complete)) return 'text/plain'
  return null
}

/** 保守的文本判定：无 NUL + UTF-8 可解码 + 控制字符比例低。 */
export function looksLikeText(bytes: Uint8Array, complete = true): boolean {
  const limit = Math.min(bytes.length, 4096)
  if (limit === 0) return false
  let controls = 0
  for (let i = 0; i < limit; i += 1) {
    const byte = bytes[i]
    if (byte === 0) return false
    // \t \n \r 之外的可打印 ASCII 之外的控制字符一律视为二进制信号。
    if (byte < 0x20 && byte !== 9 && byte !== 10 && byte !== 13) controls += 1
  }
  if (controls / limit > 0.05) return false
  try {
    // 探测前缀可停在多字节字符中间；完整输入的截断编码仍须拒绝。
    new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(0, limit), {stream: !complete || limit < bytes.length})
    return true
  } catch {
    return false
  }
}

/** 扩展名 -> 媒体类型（无声明时用）。 */
export function typeForExtension(ext: string): string | null {
  if (!ALLOWED_NON_IMAGE.has(ext)) return null
  switch (ext) {
    case 'md':
    case 'markdown':
      return 'text/markdown'
    case 'csv':
      return 'text/csv'
    case 'json':
    case 'jsonl':
      return 'application/json'
    case 'xml':
      return 'application/xml'
    case 'yaml':
    case 'yml':
      return 'application/x-yaml'
    case 'pdf':
      return 'application/pdf'
    default:
      return 'text/plain'
  }
}

/**
 * 准入单个文件：先看字节魔数，再看扩展名黑名单，最后看白名单。
 *
 * 顺序刻意如此：即使内容是**合法** PNG，`.ps1` 也不放行。反过来做的话
 * 一个改名的可执行文件会从后门进来。
 */
export function admitFile(name: string, bytes: Uint8Array, size = bytes.length): AdmissionResult {
  if (size <= 0) return { ok: false, code: 'attachment/empty', message: {key: "ui.lib.attachments.1bdb05a4e82b"} }
  if (size > MAX_ATTACHMENT_BYTES) {
    return {
      ok: false,
      code: 'attachment/too-large',
      message: {key: "ui.lib.attachments.35cd8a7fd9be", params: {p0: formatSize(size), p1: formatSize(MAX_ATTACHMENT_BYTES)}},
    }
  }

  const ext = extensionOf(name)
  if (ext && DENIED_EXTENSIONS.has(ext)) {
    return { ok: false, code: 'attachment/type-denied', message: {key: "ui.lib.attachments.40adad246a3f", params: {p0: ext}} }
  }

  const sniffed = sniffMediaType(bytes, bytes.length >= size)
  if (sniffed && sniffed.startsWith('image/')) {
    if (ext) {
      // 扩展名必须在该媒体类型的合法集合里，否则命名与内容打架。
      const allowed = IMAGE_EXTENSIONS[sniffed]
      if (!allowed || !allowed.has(ext)) {
        return {
          ok: false,
          code: 'attachment/unsupported',
          message: {key: "ui.lib.attachments.f6703267b814", params: {p0: sniffed, p1: ext}},
        }
      }
    }
    return { ok: true, mediaType: sniffed, isImage: true }
  }

  if (ext) {
    const mapped = typeForExtension(ext)
    if (mapped) return { ok: true, mediaType: mapped, isImage: false }
    return {
      ok: false,
      code: 'attachment/unsupported',
      message: {key: "ui.lib.attachments.40adad246a3f", params: {p0: ext}},
    }
  }

  if (sniffed === 'text/plain') {
    return { ok: true, mediaType: 'text/plain', isImage: false }
  }
  if (sniffed === 'application/pdf') {
    return { ok: true, mediaType: 'application/pdf', isImage: false }
  }
  return { ok: false, code: 'attachment/unsupported', message: {key: "ui.lib.attachments.f25703aa90d5", params: {p0: name}} }
}

/** 限额检查独立成函数，便于给出"还能再拖几个"这类提示。 */
export function describeBudget(used: DraftAttachment[]): {
  remainingFiles: number
  remainingBytes: number
} {
  const bytes = used.reduce((sum, item) => sum + item.size, 0)
  return {
    remainingFiles: Math.max(0, MAX_BATCH_FILES - used.length),
    remainingBytes: Math.max(0, MAX_BATCH_BYTES - bytes),
  }
}

/** 为草稿条目生成本地 id（不依赖 crypto.randomUUID 的可用性）。 */
let counter = 0
export function nextLocalId(): string {
  counter += 1
  return `att-${counter.toString(36)}-${(counter * 2654435761) % 46656}`
}

/** 人类可读大小。 */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** 截图类图片值得内联预览，其它类型只给图标。 */
export function isPreviewable(mediaType: string): boolean {
  return mediaType.startsWith('image/')
}

/**
 * 从拖放/粘贴/选择器拿到的 `DataTransfer` 里挑出文件。
 *
 * 拖进来一整个文件夹时 `items` 里会混进非文件项，必须过滤掉，否则
 * `fileFromDataTransfer` 拿到 null，后面就会崩在一个莫名其妙的地方。
 */
/**
 * 这个 drag 事件是否携带文件。
 *
 * 拖选文本、拖链接内部锚点都会触发 dragenter/dragover。如果不判类型就
 * 一律接管，用户拖一段文字进输入区会被吞掉、遮罩还会闪一下 —— 这是
 * "全局拖放"最常见的误伤。`types` 里出现 `Files` 是唯一可靠信号。
 */
export function dragCarriesFiles(types: ArrayLike<string> | null | undefined): boolean {
  if (!types) return false
  for (const type of Array.from(types)) {
    if (type === 'Files') return true
  }
  return false
}

export function filesFromDataTransfer(transfer: DataTransfer | null): File[] {
  if (!transfer) return []
  if (transfer.files && transfer.files.length > 0) return Array.from(transfer.files)
  const items = transfer.items
  if (!items) return []
  const out: File[] = []
  for (const item of Array.from(items)) {
    if (item.kind !== 'file') continue
    const file = item.getAsFile()
    if (file) out.push(file)
  }
  return out
}

/** 粘贴板里的图片：截图工具复制出来的是 `image/png`，没有文件名。 */
export function isPastedImage(file: File): boolean {
  return file.type.startsWith('image/') && (!file.name || file.name === 'image.png')
}

/** 给粘贴的截图补一个可读名字 —— 无名附件在草稿轨上无法区分。 */
export function nameForPastedImage(file: File, index: number): string {
  const ext = extensionOf(file.name) || file.type.split('/')[1] || 'png'
  return `pasted-image-${index}.${ext}`
}

/** Uint8Array -> base64（分块，避免大文件 apply 爆栈）。 */
export function bytesToBase64(bytes: Uint8Array): string {
  const CHUNK = 0x8000
  let binary = ''
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK))
  }
  return btoa(binary)
}

/** 移除某个本地 id 的附件。 */
export function removeDraft(drafts: DraftAttachment[], localId: string): DraftAttachment[] {
  const target = drafts.find((item) => item.localId === localId)
  if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl)
  return drafts.filter((item) => item.localId !== localId)
}

/** 清空草稿并回收所有 object URL —— 不回收就是内存泄漏。 */
export function clearDrafts(drafts: DraftAttachment[]): DraftAttachment[] {
  for (const item of drafts) {
    if (item.previewUrl) URL.revokeObjectURL(item.previewUrl)
  }
  return []
}

/** 还有未上传完成的附件时不允许发送。 */
export function canSendWithDrafts(drafts: DraftAttachment[]): boolean {
  return !drafts.some((item) => item.status === 'uploading' || item.status === 'pending')
}

/** 按原始正文核对回显；仅额外识别宿主 /goal 正文变换。 */
export function matchesLocalEcho(localText: string, serverPrompt: string): boolean {
  if (localText === serverPrompt) return true
  // /goal creates the goal before the agent receives its objective. Reconcile
  // that transformed echo with the original optimistic command, too.
  const goal = localText.trim().match(/^\/goal\s+([\s\S]+)$/)?.[1].trim()
  if (goal && !/^(?:status|help|edit|pause|resume|clear|delete)$/.test(goal) && !/^edit\s/.test(goal) &&
      goal === serverPrompt.trim()) return true
  return false
}
