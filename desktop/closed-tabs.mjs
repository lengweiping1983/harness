import {websiteURL} from './browser-policy.mjs'

export class ClosedTabs {
  constructor() {this.entries = new Map(); this.generations = new Map()}
  capture(owner) {
    if (!this.generations.has(owner)) this.generations.set(owner, Symbol(owner))
    return this.generations.get(owner)
  }
  isCurrent(owner, generation) {return this.generations.get(owner) === generation}
  remember(owner, url) {
    try {url = websiteURL(url)} catch {return}
    const entries = this.entries.get(owner) || []
    entries.push(url)
    this.entries.set(owner, entries.slice(-20))
  }
  has(owner) {return !!this.entries.get(owner)?.length}
  take(owner) {
    const entries = this.entries.get(owner)
    const url = entries?.pop()
    if (!entries?.length) this.entries.delete(owner)
    return url
  }
  clear(owner) {this.entries.delete(owner); this.generations.delete(owner)}
  clearAll() {this.entries.clear(); this.generations.clear()}
}
