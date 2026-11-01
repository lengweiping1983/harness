/** 只加载模拟器代码，不创建终端或发起任何进程写入。 */
let pending: Promise<[typeof import('@xterm/xterm'), typeof import('@xterm/addon-fit')]> | undefined
export function loadTerminalEmulator() {
  return pending ??= Promise.all([import('@xterm/xterm'), import('@xterm/addon-fit')]).catch(error => {
    pending = undefined
    throw error
  })
}
