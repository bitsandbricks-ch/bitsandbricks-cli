// Small shared helpers: confirmation prompts, time, links in text.
import { reactive } from 'vue'

export const prompt = reactive({ open: false, question: '', detail: '', resolve: null, allowDry: true, input: null })

// ask('Merge acme/web#7 now?') -> 'yes' | 'dry' | null
export function ask(question, { detail = '', allowDry = true, input = null } = {}) {
  return new Promise((resolve) => {
    Object.assign(prompt, { open: true, question, detail, allowDry, input, resolve })
  })
}

export function answer(value) {
  const r = prompt.resolve
  Object.assign(prompt, { open: false, resolve: null })
  if (r) r(value)
}

export function ago(stamp) {
  if (!stamp) return '-'
  const secs = (Date.now() - new Date(stamp.endsWith('Z') || stamp.includes('+') ? stamp : stamp + 'Z')) / 1000
  for (const [u, s] of [['d', 86400], ['h', 3600], ['m', 60]]) if (secs >= s) return `${Math.floor(secs / s)}${u} ago`
  return 'just now'
}

const URL_RE = /(https?:\/\/[^\s)\]>,'"]+)/g

// Text -> [{text}|{href,label}] so templates can render links without v-html.
export function linkify(text) {
  const out = []
  let last = 0
  for (const m of String(text || '').matchAll(URL_RE)) {
    if (m.index > last) out.push({ text: text.slice(last, m.index) })
    out.push({ href: m[0], label: shortLink(m[0]) })
    last = m.index + m[0].length
  }
  if (last < String(text || '').length) out.push({ text: String(text).slice(last) })
  return out
}

export function shortLink(url) {
  return url.replace(/^https:\/\/(github\.com|gitlab\.com)\//, '').replace(/^https?:\/\//, '')
}

export function stateClass(value) {
  const v = String(value || '').toLowerCase()
  if (/^(success|passing|ok|approved|merged|verified|ready|green)/.test(v)) return 'ok'
  if (/(fail|error|conflict|critical|high|secret|denied|unverified|missing|blocked)/.test(v)) return 'bad'
  if (/(pending|running|waiting|draft|medium|moderate|manual|review)/.test(v)) return 'warn'
  return ''
}
