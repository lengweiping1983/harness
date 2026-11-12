import {spawn, execFile} from 'node:child_process'
import {fileURLToPath} from 'node:url'
import {mkdtemp, readFile, rm, rmdir} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {promisify} from 'node:util'

export function captureHelperPath(filename) {
  return filename.replace(/([\\/])app\.asar([\\/])/, '$1app.asar.unpacked$2')
}

/** Prepare the Windows helper without capturing or caching any screen content. */
export class FastScreenCapture {
  constructor() {
    this.child = null; this.waiter = null; this.closed = false
    if (process.platform === 'win32') this.start()
  }
  start() {
    this.ready = new Promise((resolve, reject) => {
      const executable = path.join(process.env.SystemRoot || 'C:\\Windows','System32/WindowsPowerShell/v1.0/powershell.exe')
      const child = spawn(executable,['-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',captureHelperPath(fileURLToPath(new URL('./capture-screen.ps1',import.meta.url)))],{windowsHide:true,stdio:['pipe','pipe','pipe']})
      this.child = child
      let header = '', payload = null, received = 0
      const timer = setTimeout(() => {child.kill();reject(Error('Capture helper startup timed out'))},10000)
      child.stdout.on('data',chunk => {
        while (chunk.length) {
          if (!payload) {
            const newline = chunk.indexOf(10)
            if (newline < 0) {header += chunk.toString('ascii');if(header.length>100)child.kill();return}
            const line = (header + chunk.subarray(0,newline).toString('ascii')).trim();header='';chunk=chunk.subarray(newline+1)
            if (line === 'ready') {clearTimeout(timer);resolve();continue}
            const size = Number(line)
            if (!Number.isInteger(size) || size < 54 || size > 100*1024*1024) {
              const waiter=this.waiter;this.waiter=null;if(waiter){clearTimeout(waiter.timer);waiter.reject(Error('Screen capture failed'))}child.kill();return
            }
            payload=Buffer.allocUnsafe(size);received=0
          }
          const length=Math.min(chunk.length,payload.length-received)
          chunk.copy(payload,received,0,length);received+=length;chunk=chunk.subarray(length)
          if(received===payload.length){const waiter=this.waiter;this.waiter=null;const data=payload;payload=null;if(waiter){clearTimeout(waiter.timer);waiter.resolve(data)}}
        }
      })
      const failed = () => {
        clearTimeout(timer);reject(Error('Capture helper stopped'))
        const waiter = this.waiter;this.waiter = null
        if (waiter) {clearTimeout(waiter.timer);waiter.reject(Error('Capture helper stopped'))}
        if (this.child === child) this.child = null
      }
      child.on('error',failed);child.on('exit',failed)
      child.stdin.on('error',failed)
      child.stderr.on('data', data => {if (process.env.HARNESS_CAPTURE_PROFILE) process.stderr.write(data)})
    })
    this.ready.catch(() => {})
  }
  async capture(display, screen) {
    if (this.closed) throw Error('Capture helper closed')
    if (process.platform === 'win32') {
      if (!this.child) this.start()
      await this.ready
      if (this.waiter) throw Error('Capture already pending')
      const rect = screen.dipToScreenRect(null, display.bounds)
      const bitmap = await new Promise((resolve,reject) => {
        const timer = setTimeout(() => {this.waiter=null;this.child?.kill();reject(Error('Screen capture timed out'))},5000)
        this.waiter = {resolve,reject,timer}
        this.child.stdin.write([rect.x,rect.y,rect.width,rect.height].join(',')+'\n')
      })
      const width=bitmap.readInt32LE(18), height=bitmap.readInt32LE(22), offset=bitmap.readUInt32LE(10)
      const stride=(width*3+3)&~3
      if(width!==rect.width || height!==rect.height || bitmap.readUInt16LE(28)!==24 || offset+stride*height>bitmap.length) throw Error('Invalid screenshot bitmap')
      return {bytes:bitmap,type:'image/bmp'}
    }
    if (process.platform === 'darwin') {
      const directory = await mkdtemp(path.join(tmpdir(),'harness-screen-'))
      const file = path.join(directory,'capture.png'), r = display.bounds
      try {
        await promisify(execFile)('/usr/sbin/screencapture',['-x','-t','png','-R',`${r.x},${r.y},${r.width},${r.height}`,file],{timeout:5000})
        return await readFile(file)
      } finally {await rm(file,{force:true});await rmdir(directory)}
    }
    throw Error('Fast capture unavailable')
  }
  close() {this.closed=true;this.child?.kill()}
}
