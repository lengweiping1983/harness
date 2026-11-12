/** Keep a recoverable URL when Chromium replaces or loses the document. */
export class TabHealth {
  constructor(url = 'about:blank') {
    this.url = url
    this.issue = null
  }
  navigating(url) {
    if (url === 'about:blank' || /^https?:\/\//i.test(url)) {
      this.url = url
      this.issue = null
    }
  }
  failed(code, mainFrame) {
    if (mainFrame && code !== -3) this.issue = 'load-failed'
  }
  gone(reason) {
    this.issue = reason === 'clean-exit' ? 'closed' : 'crashed'
  }
  unresponsive() { this.issue = 'unresponsive' }
  responsive() { if (this.issue === 'unresponsive') this.issue = null }
}
