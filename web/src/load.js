import { onMounted, ref, watch } from 'vue'
import { get, log, post, qs, state } from './api.js'
import { scope } from './scope.js'
import { ask } from './ui.js'

// Answers the views already have, by request path: switching tabs (or going back from a page) shows the
// last answer at once and asks again only when it is older than FRESH_MS. r and every change ask again.
const memo = new Map() // path -> {at, data}
const FRESH_MS = 60_000
const MAX = 200

function remember(path, data) {
  memo.delete(path)
  memo.set(path, { at: Date.now(), data })
  while (memo.size > MAX) memo.delete(memo.keys().next().value)
}

// Drop remembered answers whose path starts with prefix (all of them without one).
export function forget(prefix = '') {
  for (const k of [...memo.keys()]) if (k.startsWith(prefix)) memo.delete(k)
}

// A GET through the memo: a remembered answer younger than FRESH_MS, unless fresh.
export async function cached(path, fresh = false) {
  const key = path.replace(/[?&]fresh=1/, '')
  const hit = memo.get(key)
  if (!fresh && hit && Date.now() - hit.at < FRESH_MS) return hit.data
  const data = await get(path)
  remember(key, data)
  return data
}

// Load a list when the view opens (and again on r, or when the provider/group changes). The fetcher returns
// the request path; a remembered answer shows right away and is asked again when older than FRESH_MS.
export function useLoad(fetcher, { scoped = true } = {}) {
  const data = ref(null)
  const error = ref('')
  const loading = ref(false)
  async function load(fresh = false) {
    const q = scoped ? qs({ provider: scope.provider === 'all' ? '' : scope.provider, group: scope.group, fresh }) : ''
    const got = fetcher(q, fresh) // a path (remembered, see cached) or a promise
    const key = typeof got === 'string' ? got.replace(/[?&]fresh=1/, '') : null
    const hit = key && memo.get(key)
    if (!fresh && hit) {
      data.value = hit.data
      if (Date.now() - hit.at < FRESH_MS) return
    }
    loading.value = true
    error.value = ''
    try {
      data.value = key ? await cached(got, true) : await got
    } catch (e) {
      error.value = e.message
      log(e.message, 'error')
    } finally {
      loading.value = false
    }
  }
  onMounted(() => load())
  if (scoped) watch(() => [scope.provider, scope.group], () => load())
  return { data, error, loading, load }
}

// A change: ask first (y / d for a dry run / n), then send it; returns the command output or null.
export async function act(question, path, body = {}, { detail = '' } = {}) {
  const a = await ask(question, { detail: detail || (state.dryRun ? 'dry run is on: nothing changes' : '') })
  if (!a) return null
  try {
    const got = await post(path, { ...body, dry_run: a === 'dry' })
    if (a !== 'dry') forget() // a change: every remembered answer may be stale now
    log(`${a === 'dry' ? '[dry-run] ' : ''}${question.replace(/\?$/, '')}: done`, 'ok')
    return got.text ?? ''
  } catch (e) {
    log(e.message, 'error')
    return `error: ${e.message}`
  }
}
