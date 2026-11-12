export class TabSelections extends Map {
  constructor() {super(); this.history = new Map()}
  set(owner, id) {
    if (id !== null) this.history.set(owner, [...(this.history.get(owner) || []).filter(value => value !== id), id])
    return super.set(owner, id)
  }
  forget(owner, id, available) {
    const remaining = new Set(available)
    const history = (this.history.get(owner) || []).filter(value => value !== id && remaining.has(value))
    this.history.set(owner, history)
    if (this.get(owner) === id) super.set(owner, history.at(-1) ?? available.at(-1) ?? null)
  }
  delete(owner) {this.history.delete(owner); return super.delete(owner)}
}
