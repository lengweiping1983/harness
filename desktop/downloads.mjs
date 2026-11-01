import {randomUUID} from 'node:crypto'
import {mkdirSync, openSync, closeSync, unlinkSync, fstatSync, lstatSync} from 'node:fs'
import path from 'node:path'
import {NativeBrowserError} from './browser-errors.mjs'

export function safeFilename(value) {
  let name = String(value || 'download').replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').replace(/[. ]+$/g, '')
  while (Buffer.byteLength(name, 'utf8') > 180) name = Array.from(name).slice(0, -1).join('')
  name = name.replace(/[. ]+$/g, '')
  if (!name || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name)) name = 'download-' + (name || 'file')
  return name
}
export function reserveDownload(directory, filename) {
  mkdirSync(directory, {recursive:true})
  const safe = safeFilename(filename)
  const extension = path.extname(safe)
  const stem = safe.slice(0, safe.length - extension.length)
  for (let index = 0; index < 10000; index++) {
    const destination = path.join(directory, index ? `${stem} (${index})${extension}` : safe)
    let descriptor
    try {descriptor = openSync(destination, 'wx')} catch (error) {if (error.code === 'EEXIST') continue; throw error}
    let identity
    try {identity = fstatSync(descriptor, {bigint:true})} catch (error) {
      try {closeSync(descriptor)} catch (cleanup) {throw new AggregateError([error, cleanup], 'browser/download-reservation-cleanup', {cause:error})}
      throw error
    }
    closeSync(descriptor)
    return {path:destination, identity}
  }
  throw new NativeBrowserError('downloadNameLimit')
}

function removeReservation(reservation) {
  let current
  try {current = lstatSync(reservation.path, {bigint:true})} catch (error) {
    if (error.code === 'ENOENT') {
      // Windows 会把“父路径不是目录”一并报为 ENOENT；核对父路径身份，
      // 避免目录被替换成文件时把清理失败误判为预留文件正常消失。
      let parentEntry
      try {parentEntry = lstatSync(path.dirname(reservation.path), {bigint:true})} catch (parentError) {throw parentError}
      if (parentEntry.isDirectory()) return
    }
    throw error
  }
  const {identity} = reservation
  if (current.isFile() && current.dev === identity.dev && current.ino === identity.ino && current.birthtimeNs === identity.birthtimeNs) unlinkSync(reservation.path)
}

function downloadFailure(error) {
  if (error instanceof NativeBrowserError) return error.copy
  if (error instanceof AggregateError && error.errors.length) return error.errors.map(downloadFailure).reduce((primary, secondary) => ({template:{key:'browser.desktop.downloadCombinedFailure', params:{primary, secondary}}}))
  return error?.message ?? String(error)
}

export class BrowserDownloads {
  constructor(directory, changed = () => {}) {this.directory = directory; this.changed = changed; this.records = new Map()}
  snapshot(owner) {return [...this.records.values()].filter(record => record.owner === owner).reverse().map(({item, owner, ...state}) => state)}
  add(item, owner) {
    const id = randomUUID()
    if ([...this.records.values()].filter(record => !record.done).length >= 10) {
      item.cancel()
      this.records.set(id, {id, owner, item:null, filename:safeFilename(item.getFilename()), path:'',
        source:item.getInitiatorOrigin(), received:0, total:0, state:'interrupted', reason:'limit', paused:false, resumable:false, done:true})
      this.prune(); this.changed(); return id
    }
    let reservation
    try {reservation = reserveDownload(typeof this.directory === 'function' ? this.directory() : this.directory, item.getFilename()); item.setSavePath(reservation.path)} catch (error) {
      if (reservation) try {removeReservation(reservation)} catch (cleanup) {error = new AggregateError([error, cleanup], 'browser/download-reservation-cleanup', {cause:error})}
      const filename = safeFilename(item.getFilename())
      const source = item.getInitiatorOrigin()
      item.cancel()
      this.records.set(id, {id, owner, item:null, filename, path:'', source, received:0, total:0, state:'interrupted', paused:false, resumable:false, done:true, error:downloadFailure(error)})
      this.prune(); this.changed(); return id
    }
    const destination = reservation.path
    const record = {id, owner, item, filename:path.basename(destination), path:destination, source:item.getInitiatorOrigin(), received:0, total:item.getTotalBytes(), state:'progressing', paused:false, resumable:false, done:false}
    this.records.set(id, record)
    const update = state => {
      Object.assign(record, {state, received:item.getReceivedBytes(), total:item.getTotalBytes(), paused:item.isPaused(), resumable:item.canResume()})
      this.changed()
    }
    item.on('updated', (_event, state) => update(state))
    item.once('done', (_event, state) => {
      update(state)
      record.done = true
      record.item = null
      record.resumable = false
      if (state !== 'completed') try {removeReservation(reservation)} catch (error) {record.error = {template:{key:'browser.desktop.downloadCleanupFailure', params:{error:downloadFailure(error)}}}}
      this.prune()
      this.changed()
    })
    this.prune()
    this.changed()
    return id
  }
  prune() {
    for (const [id, record] of this.records) {if (this.records.size <= 100) break; if (record.done) this.records.delete(id)}
  }
  close() {for (const record of this.records.values()) if (!record.done && record.item) record.item.cancel()}
  action(owner, id, action) {
    const record = this.records.get(id)
    if (!record || record.owner !== owner) throw new NativeBrowserError('downloadMissing')
    if (action === 'show') {if (record.state !== 'completed' || !record.done) throw new NativeBrowserError('downloadIncomplete'); return record.path}
    if (record.done || !record.item) throw new NativeBrowserError('downloadStopped')
    if (action === 'cancel') record.item.cancel()
    else if (action === 'pause' && record.state === 'progressing') record.item.pause()
    else if (action === 'resume' && (record.paused || record.resumable)) record.item.resume()
    else throw new NativeBrowserError('downloadAction')
    this.changed()
  }
}
