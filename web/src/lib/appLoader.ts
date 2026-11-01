let pending: ReturnType<typeof importApp> | undefined
function importApp() {
  return Promise.all([
    import('../App'),
    import('../store/app').then(({useAppStore}) => {
      const state = useAppStore.getState()
      // 目录读取与界面模块下载并行；写操作和恢复对话仍由原入口准入。
      if (!state.booted && !state.booting) void state.boot()
    }),
  ]).then(([module]) => module)
}

/** 启动引导和 React 入口共用一次请求，语言下载与应用依赖下载并行。 */
export function loadApp() {
  return pending ??= importApp()
}
