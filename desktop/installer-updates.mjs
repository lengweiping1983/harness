const MAX_RELEASE_BYTES = 1024 * 1024
class UpdateFailure extends Error {
  constructor(menuKey, params = {}, cause) {super(menuKey, {cause}); this.menuKey = menuKey; this.params = params}
}

function versionParts(value) {
  if (typeof value !== 'string' || !/^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.test(value)) throw new UpdateFailure('updateInvalidRelease')
  const parts = value.split('.').map(Number)
  if (parts.some(part => !Number.isSafeInteger(part))) throw new UpdateFailure('updateInvalidRelease')
  return parts
}

export function newerVersion(candidate, current) {
  const left = versionParts(candidate), right = versionParts(current)
  for (let index = 0; index < left.length; index++) if (left[index] !== right[index]) return left[index] > right[index]
  return false
}

async function readRelease(response) {
  const chunks = []
  let size = 0
  for await (const chunk of response.body) {
    size += chunk.byteLength
    if (size > MAX_RELEASE_BYTES) throw new UpdateFailure('updateMetadataTooLarge')
    chunks.push(chunk)
  }
  try {return JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(Buffer.concat(chunks)))}
  catch (cause) {throw new UpdateFailure('updateInvalidRelease', {}, cause)}
}

/** GitHub 只发布安装包时直接读取稳定发行，不依赖 ZIP 或更新清单。 */
export class InstallerUpdates {
  constructor({source, version, platform = process.platform, arch = process.arch, packaged, fetch = globalThis.fetch, notify = () => {}, installer = null}) {
    if (!source || source.provider !== 'github' || !/^[A-Za-z0-9_-]+$/.test(source.owner) || !/^[A-Za-z0-9_.-]+$/.test(source.repo)) throw Error('Invalid update repository')
    versionParts(version)
    this.source = {...source}
    this.current = version
    this.platform = platform
    this.arch = arch
    this.packaged = packaged
    this.fetch = fetch
    this.notify = notify
    this.installer = installer
    this.progress = null
    this.phase = null
    this.received = 0
    this.total = null
    this.asset = null
    this.state = packaged ? 'idle' : 'development'
    this.pending = null
    this.version = null
    this.downloadUrl = null
    this.reason = null
  }
  snapshot() {return {state:this.state, version:this.version, progress:this.progress, phase:this.phase, received:this.received, total:this.total, mode:'installer', downloadUrl:this.downloadUrl, reason:this.reason}}
  setState(state) {this.state = state; this.notify(state)}
  check() {
    if (!this.packaged) return Promise.resolve(this.snapshot())
    if (this.pending) return this.pending
    if (this.state === 'downloaded') return Promise.resolve(this.snapshot())
    this.asset = null
    this.progress = this.phase = this.total = null
    this.received = 0
    this.version = this.downloadUrl = this.reason = null
    this.pending = Promise.resolve().then(async () => {
      const {owner, repo} = this.source
      const response = await this.fetch(`https://api.github.com/repos/${owner}/${repo}/releases/latest`, {
        headers:{Accept:'application/vnd.github+json', 'User-Agent':'Harness'}, signal:AbortSignal.timeout(15000), redirect:'error',
      })
      if (response.status === 404) {
        await response.body?.cancel()
        this.reason = 'unpublished'
        this.setState('unavailable')
        return this.snapshot()
      }
      if (!response.ok) {await response.body?.cancel(); throw new UpdateFailure('updateHttpFailure', {status:response.status})}
      const release = await readRelease(response)
      if (!release || release.draft !== false || release.prerelease !== false || typeof release.tag_name !== 'string' || !release.tag_name.startsWith('v') || !Array.isArray(release.assets)) throw new UpdateFailure('updateInvalidRelease')
      this.version = release.tag_name.slice(1)
      if (!newerVersion(this.version, this.current)) {this.setState('current'); return this.snapshot()}
      const os = {darwin:'mac', win32:'win'}[this.platform]
      const extension = {darwin:'dmg', win32:'exe'}[this.platform]
      const name = `Harness-${this.version}-${os}-${this.arch}.${extension}`
      const expected = `https://github.com/${owner}/${repo}/releases/download/${release.tag_name}/${name}`
      const assets = release.assets.filter(asset => asset?.name === name)
      if (assets.length === 0) {this.reason = 'platform'; this.setState('unavailable'); return this.snapshot()}
      if (assets.length !== 1 || assets[0].state !== 'uploaded' || !Number.isSafeInteger(assets[0].size) || assets[0].size <= 0 || assets[0].browser_download_url !== expected) throw new UpdateFailure('updateInvalidRelease')
      this.asset = {...assets[0]}
      this.downloadUrl = expected
      this.setState('available')
      return this.snapshot()
    }).catch(error => {this.setState('error'); throw error}).finally(() => {this.pending = null})
    this.setState('checking')
    return this.pending
  }
  download() {
    if (this.pending) return this.state === 'downloading' ? this.pending : Promise.reject(new UpdateFailure('updateInvalidRelease'))
    if (this.state !== 'available' || !this.installer || !this.asset) return Promise.reject(new UpdateFailure('updateInvalidRelease'))
    this.progress = null
    this.phase = 'connecting'
    this.received = 0
    this.total = this.asset.size
    this.pending = Promise.resolve().then(async () => {
      await this.installer.prepare(this.asset, this.version, (progress, details = {}) => {
        this.progress = progress
        this.phase = details.phase || 'downloading'
        if (details.received !== undefined) this.received = details.received
        this.notify(this.state)
      })
      this.progress = 100
      this.phase = 'ready'
      this.setState('downloaded')
      return this.snapshot()
    }).catch(error => {this.setState('error'); throw error}).finally(() => {this.pending = null})
    this.setState('downloading')
    return this.pending
  }
  async close({install = false} = {}) {
    if (this.state === 'downloading') {
      this.installer.cancel()
      // 下载调用方保留原失败；退出只等待所属任务收尾。
      await this.pending.catch(() => {})
    }
    if (!install) await this.installer?.discard?.()
  }
}
