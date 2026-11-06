// Keys like the TUI's: a view registers its own with useKeys while it is shown.
import { onBeforeUnmount, onMounted } from 'vue'
import { prompt } from './ui.js'

export function typing(e) {
  const t = e.target
  return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)
}

export function useKeys(map) {
  function on(e) {
    if (prompt.open || typing(e) || e.ctrlKey || e.metaKey || e.altKey) return
    const fn = map[e.key]
    if (fn) { e.preventDefault(); fn(e) }
  }
  onMounted(() => window.addEventListener('keydown', on))
  onBeforeUnmount(() => window.removeEventListener('keydown', on))
}
