import { reactive } from 'vue'
import { get } from './api.js'

// Shared by every list: the provider and group filter, the groups of the defaults, and the orgs (GitHub)
// and GitLab groups the repos are in. An org filters as `acme/` or `gitlab:grp/`.
export const scope = reactive({ provider: 'all', group: '', groups: [], owners: [] })

// How an org filter reads in a menu: acme/ -> org acme, gitlab:a/b/ -> gitlab a/b.
export function ownerLabel(o) {
  return o.startsWith('gitlab:') ? `gitlab ${o.slice(7, -1)}` : `org ${o.slice(0, -1)}`
}

let loading = null
export function loadGroups() {
  if (!loading) loading = get('/repos').then((r) => { scope.groups = r.groups || []; scope.owners = r.owners || []; return r }).catch(() => { loading = null })
  return loading
}
