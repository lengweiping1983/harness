const stopping = new WeakMap()

export function stopChild(child, {graceMs = 3000, killMs = 3000} = {}) {
  if (!child || child.exitCode !== null || child.signalCode !== null || child.spawnfile && child.pid === undefined) return Promise.resolve()
  const current = stopping.get(child)
  if (current) return current
  const pending = new Promise((resolve, reject) => {
    let timer
    const finish = error => {
      clearTimeout(timer)
      child.removeListener('exit', exited)
      if (error) reject(error)
      else resolve()
    }
    const exited = () => finish()
    child.once('exit', exited)
    timer = setTimeout(() => {
      timer = setTimeout(() => finish(Error('Backend process did not exit after SIGKILL')), killMs)
      try {child.kill('SIGKILL')} catch (error) {finish(error)}
    }, graceMs)
    try {child.kill('SIGINT')} catch (error) {finish(error)}
  })
  const owned = pending.finally(() => {
    if (stopping.get(child) === owned) stopping.delete(child)
  })
  stopping.set(child, owned)
  return owned
}
