import { reactive } from 'vue'
import { get } from './api.js'

// Shared by every list: the provider and group filter, and the groups of the defaults.
export const scope = reactive({ provider: 'all', group: '', groups: [] })

let loading = null
export function loadGroups() {
  if (!loading) loading = get('/repos').then((r) => { scope.groups = r.groups || []; return r }).catch(() => { loading = null })
  return loading
}
