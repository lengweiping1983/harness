/** macOS 使用原生应用图标，其他平台沿 Electron 的 iconPath 参数加载图标。 */
export function configureAboutPanel(app, {iconPath, platform = process.platform}) {
  app.setAboutPanelOptions({
    applicationName: 'Harness',
    applicationVersion: app.getVersion(),
    ...(platform === 'darwin' ? {} : {iconPath}),
  })
}
