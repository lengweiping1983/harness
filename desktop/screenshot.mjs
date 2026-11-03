/** One frozen screen image; never keep a sharing stream alive. */
export class ScreenshotSession {
  constructor({window, screen, desktopCapturer, authorize, captureScreen, publishImage, releaseImage, suspend, resume, delay = ms => new Promise(resolve => setTimeout(resolve, ms))}) {
    Object.assign(this, {window, screen, desktopCapturer, authorize, captureScreen, publishImage, releaseImage, suspend, resume, delay})
    this.active = false
    this.cleanupFailure = null
    this.snapshot = null
    this.generation = 0
  }
  async capture(hideWindow = false) {
    if (typeof hideWindow !== 'boolean') throw Error('Invalid screenshot option')
    if (this.cleanupFailure) throw this.cleanupFailure
    if (this.active) throw Error('Screenshot already active')
    this.active = true
    const generation = ++this.generation
    const window = this.window
    try {
      if (this.authorize) {
        const permission = await this.authorize()
        if (generation !== this.generation || window.isDestroyed()) return null
        if (!permission.granted) {this.active = false; return {permission:permission.copy}}
      }
      this.snapshot = {bounds:window.getBounds(), normalBounds:window.getNormalBounds?.() || window.getBounds(), maximized:window.isMaximized(), fullscreen:window.isFullScreen(), simpleFullscreen:window.isSimpleFullScreen(), alwaysOnTop:window.isAlwaysOnTop()}
      const display = this.screen.getDisplayMatching(this.snapshot.bounds)
      if (hideWindow) {window.hide(); await this.delay(process.platform === 'win32' ? 50 : 150)}
      if (generation !== this.generation) return null
      const size = {width:Math.round(display.size.width * display.scaleFactor), height:Math.round(display.size.height * display.scaleFactor)}
      let data
      if (this.captureScreen) data = await this.captureScreen(display, this.screen)
      else {
      let timeout
      const sources = await Promise.race([
        this.desktopCapturer.getSources({types:['screen'], thumbnailSize:size, fetchWindowIcons:false}),
        new Promise((_, reject) => {timeout = setTimeout(() => reject(Error('Screen capture timed out')), 10000)})
      ]).finally(() => clearTimeout(timeout))
      if (generation !== this.generation || window.isDestroyed()) return null
      const source = sources.find(item => item.display_id === String(display.id)) || (sources.length === 1 ? sources[0] : null)
      if (!source || source.thumbnail.isEmpty()) throw Error('Screen capture unavailable. Check screen recording permissions.')
      data = source.thumbnail.toPNG()
      }
      if (generation !== this.generation || window.isDestroyed()) return null
      const bytes = data?.bytes || data
      const bitmap = data?.type === 'image/bmp'
      if (this.captureScreen && !(bitmap ? bytes?.subarray(0,2).equals(Buffer.from('BM')) : bytes?.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))) throw Error('Invalid screenshot image')
      if (bytes.length > (bitmap ? 100 : 20) * 1024 * 1024) throw Error('Screenshot exceeds image limit')
      this.suspend()
      if (process.platform === 'win32' && hideWindow) {
        // A hidden Windows window may retain its old placement when entering fullscreen.
        // Anchor it to the captured monitor before showing, then enter fullscreen visibly.
        if (window.isFullScreen()) window.setFullScreen(false)
        if (window.isMaximized()) window.unmaximize()
        if (!window.isVisible?.()) window.show()
        window.setBounds(display.bounds, false)
      }
      // Simple fullscreen avoids a macOS Space transition during a short capture.
      if (process.platform === 'darwin') {if (!window.isFullScreen()) window.setSimpleFullScreen(true)}
      else window.setFullScreen(true)
      window.setAlwaysOnTop(true, 'screen-saver')
      if (!window.isVisible?.()) window.show()
      if (!window.isFocused?.()) window.focus()
      if (window.webContents) {
        const zoom = window.webContents.getZoomFactor()
        const width = Math.round(display.bounds.width / zoom), height = Math.round(display.bounds.height / zoom)
        // Native window bounds can settle before Chromium receives its resize.
        await window.webContents.executeJavaScript(`new Promise((resolve,reject)=>{
          const matches=()=>Math.abs(innerWidth-${width})<=1&&Math.abs(innerHeight-${height})<=1;
          const done=()=>{if(matches()){clearTimeout(timer);removeEventListener('resize',done);requestAnimationFrame(()=>resolve())}};
          const timer=setTimeout(()=>{removeEventListener('resize',done);reject(Error('Screenshot viewport did not cover the captured display'))},1000);
          addEventListener('resize',done);done();
        })`)
        if (generation !== this.generation || window.isDestroyed()) return null
      }
      if(this.publishImage) return this.publishImage(bytes,bitmap ? 'image/bmp' : 'image/png')
      return this.captureScreen ? {data:new Uint8Array(bytes.buffer,bytes.byteOffset,bytes.byteLength),type:bitmap ? 'image/bmp' : 'image/png'} : data.toString('base64')
    } catch (error) {
      if (generation === this.generation) {
        try { this.finish() }
        catch (cleanup) { throw new AggregateError([error, cleanup]) }
      }
      throw error
    }
  }
  finish() {
    if (this.cleanupFailure) throw this.cleanupFailure
    if (!this.active) return
    this.active = false
    this.generation++
    const errors = []
    const attempt = operation => {try {operation()} catch (error) {errors.push(error)}}
    attempt(() => this.releaseImage?.())
    const saved = this.snapshot; this.snapshot = null
    attempt(() => {
      if (!this.window.isDestroyed() && saved) {
        if (process.platform === 'darwin') {
          if (!saved.fullscreen) attempt(() => this.window.setSimpleFullScreen(saved.simpleFullscreen))
        } else attempt(() => this.window.setFullScreen(saved.fullscreen))
        attempt(() => this.window.setAlwaysOnTop(saved.alwaysOnTop))
        if (!saved.fullscreen) {
          attempt(() => this.window.setBounds(saved.maximized ? saved.normalBounds : saved.bounds))
          if (saved.maximized) attempt(() => this.window.maximize())
        }
        attempt(() => this.window.show())
        attempt(() => this.window.focus())
      }
    })
    attempt(() => this.resume())
    if (errors.length) {
      this.cleanupFailure = errors.length === 1 ? errors[0] : new AggregateError(errors)
      throw this.cleanupFailure
    }

  }
}
