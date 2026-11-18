// UI 全局态：toast 通知
let _seq = 0

export const useUiStore = defineStore('ui', {
  state: () => ({
    toasts: [] // { id, type: 'success'|'error'|'info', msg }
  }),
  actions: {
    _push(type, msg) {
      const id = ++_seq
      this.toasts.push({ id, type, msg })
      setTimeout(() => this.dismiss(id), 3600)
    },
    success(msg) {
      this._push('success', msg)
    },
    error(msg) {
      this._push('error', msg)
    },
    info(msg) {
      this._push('info', msg)
    },
    dismiss(id) {
      this.toasts = this.toasts.filter(t => t.id !== id)
    }
  }
})
