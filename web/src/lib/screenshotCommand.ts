type ScreenshotHandler = (hideWindow: boolean) => boolean
let current: ScreenshotHandler | null = null

/** 原生菜单和键盘共用当前输入区的截图入口；旧实例退出不撤销替换实例。 */
export function registerScreenshotCommand(handler: ScreenshotHandler): () => void {
  current = handler
  return () => { if (current === handler) current = null }
}

export function requestScreenshot(hideWindow = false): boolean {
  return current?.(hideWindow) ?? false
}
