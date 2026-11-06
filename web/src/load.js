import { onMounted, ref, watch } from 'vue'
import { log, post, qs, state } from './api.js'
import { scope } from './scope.js'
import { ask } from './ui.js'

// Load a list once when the view opens (and again on r, or when the provider/group changes).
export function useLoad(fetcher, { scoped = true } = {}) {
  const data = ref(null)
  const error = ref('')
  const loading = ref(false)
  async function load(fresh = false) {
    loading.value = true
    error.value = ''
    try {
      data.value = await fetcher(scoped ? qs({ provider: scope.provider === 'all' ? '' : scope.provider, group: scope.group, fresh }) : '', fresh)
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
    log(`${a === 'dry' ? '[dry-run] ' : ''}${question.replace(/\?$/, '')}: done`, 'ok')
    return got.text ?? ''
  } catch (e) {
    log(e.message, 'error')
    return `error: ${e.message}`
  }
}
