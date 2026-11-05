// Every call to the operator's /api/v1. Writes carry the session's CSRF token; errors become exceptions
// with the operator's message, which the views show in the log line.
import { reactive } from 'vue'

export const state = reactive({
  me: null,          // /api/v1/me
  loaded: false,
  dryRun: false,     // every change is a dry run while this is on
  log: [],           // [{at, text, kind}] the bottom log
  busy: 0,
  rate: null,
})

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status }
}

export function log(text, kind = 'info') {
  state.log.unshift({ at: new Date().toLocaleTimeString(), text: String(text), kind })
  if (state.log.length > 200) state.log.length = 200
}

export async function api(method, path, body) {
  const headers = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET' && state.me?.csrf) headers['X-CSRF-Token'] = state.me.csrf
  state.busy++
  try {
    const res = await fetch(`/api/v1${path}`, {
      method, headers, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body),
    })
    const data = res.status === 204 ? {} : await res.json().catch(() => ({}))
    if (!res.ok) {
      if (res.status === 401 && path !== '/me') { state.me = { user: null, providers: state.me?.providers || [] } }
      throw new ApiError(data.error || `${res.status} ${res.statusText}`, res.status)
    }
    return data
  } finally {
    state.busy--
  }
}

export const get = (path) => api('GET', path)
export const post = (path, body = {}) => api('POST', path, body)
export const put = (path, body = {}) => api('PUT', path, body)
export const del = (path) => api('DELETE', path)

export async function loadMe() {
  try { state.me = await get('/me') } catch (e) { state.me = { user: null, providers: [] }; log(e.message, 'error') }
  state.loaded = true
  return state.me
}

export function qs(params) {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '' || v === false) continue
    if (Array.isArray(v)) v.forEach((x) => p.append(k, x)); else p.append(k, v === true ? '1' : v)
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}
