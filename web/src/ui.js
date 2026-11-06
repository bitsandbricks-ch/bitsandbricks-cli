// Small shared helpers: confirmation prompts, time, links in text.
import { reactive } from 'vue'

export const prompt = reactive({ open: false, question: '', detail: '', resolve: null, allowDry: true, input: null,
  links: [], notice: false })

// ask('Merge acme/web#7 now?') -> 'yes' | 'dry' | null
export function ask(question, { detail = '', allowDry = true, input = null } = {}) {
  return new Promise((resolve) => {
    Object.assign(prompt, { open: true, question, detail, allowDry, input, resolve, links: [], notice: false })
  })
}

// tell('Revoke it at GitHub', { links: [{ href, label }] }): a message with links and an ok button.
export function tell(question, { detail = '', links = [] } = {}) {
  return new Promise((resolve) => {
    Object.assign(prompt, { open: true, question, detail, allowDry: false, input: null, resolve, links, notice: true })
  })
}

// What DELETE /me and /me/identities/:id answer about the tokens: links for those Mortar couldn't revoke.
export async function tellRevoked(got) {
  const manual = (got?.revoked || []).filter((r) => !r.revoked && r.manual)
  if (!manual.length) return
  await tell('Mortar deleted its copy of your token, but could not revoke it at the provider. Revoke it there:', {
    detail: 'The built-in GitHub sign-in has no client secret, and GitHub only lets an app revoke tokens with one.',
    links: manual.map((r) => ({ href: r.manual, label: `${r.provider} (${r.login}): ${shortLink(r.manual)}` })),
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
