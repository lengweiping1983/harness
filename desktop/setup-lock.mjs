import {setupCopy} from './setup-copy.mjs'
import {spawn} from 'node:child_process'

// The child owns an OS file lock until its input closes. The file itself is
// persistent: unlinking a lock file would let waiters lock different inodes.
const lockProgram = `
import os, sys
handle = open(sys.argv[1], 'a+b')
try:
    if os.name == 'nt':
        import msvcrt
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    handle.close()
    sys.exit(2)
print('locked', flush=True)
try:
    sys.stdin.buffer.read()
finally:
    handle.close()
`

export function acquireSetupLock(python, filename, {env = process.env, copy = setupCopy(env.HARNESS_LANGUAGE, {env})} = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, ['-I', '-u', '-c', lockProgram, filename], {env, stdio:['pipe', 'pipe', 'pipe'], windowsHide:true})
    let output = '', diagnostic = '', ready = false
    const exited = new Promise(done => child.once('exit', (code, signal) => done({code, signal})))
    child.stdin.on('error', () => {})
    child.stderr.on('data', data => {diagnostic = (diagnostic + data).slice(-4000)})
    child.once('error', reject)
    child.stdout.on('data', data => {
      output += data
      if (ready || !output.split(/\r?\n/).slice(0, -1).includes('locked')) return
      ready = true
      let releasing
      resolve({release() {
        if (!releasing) {
          child.stdin.end()
          releasing = exited.then(({code, signal}) => {
            if (code !== 0) throw Error(copy('desktop.setup.lockExited', {status: signal || code}))
          })
        }
        return releasing
      }})
    })
    child.once('exit', (code, signal) => {
      if (!ready) reject(Error(code === 2 ? copy('desktop.setup.lockBusy')
        : copy('desktop.setup.lockFailed', {status: signal || code, error: diagnostic.trim()})))
    })
  })
}
