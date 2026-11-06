<script setup>
// Open alerts: Dependabot, secret scanning and code scanning on GitHub, vulnerabilities on GitLab.
import { computed, ref } from 'vue'
import { get } from '../api.js'
import { useLoad } from '../load.js'
import { useKeys } from '../keys.js'
import { loadGroups, scope } from '../scope.js'
import DataTable from '../components/DataTable.vue'
import Scope from '../components/Scope.vue'

const { data, loading, load } = useLoad((q) => get(`/security${q}`))
loadGroups()
const sel = ref(null)
const rows = computed(() => (data.value?.security || []).flatMap((s) => s.alerts.map((a, i) => ({ ...a, repo: s.repo, provider: s.provider, key: `${s.repo}|${i}` }))))
const unread = computed(() => (data.value?.security || []).filter((s) => s.unread.length))
const counts = computed(() => rows.value.reduce((m, a) => ({ ...m, [a.severity]: (m[a.severity] || 0) + 1 }), {}))
const columns = [
  { key: 'severity', label: 'severity' }, { key: 'repo', label: 'repo', cls: (r) => (r.provider === 'gitlab' ? 'warn' : '') },
  { key: 'kind', label: 'kind', cls: () => 'dim' }, { key: 'title', label: 'alert', cls: () => '' }, { key: 'where', label: 'where', cls: () => 'dim' },
]
const open = (r) => r?.url && window.open(r.url, '_blank', 'noopener')
useKeys({ r: () => load(true) })
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <Scope :groups="scope.groups" />
      <span v-for="(n, s) in counts" :key="s" class="chip">{{ s }} {{ n }}</span>
    </div>
    <div class="scroll">
      <DataTable v-model="sel" :columns="columns" :rows="rows" :empty="loading ? 'loading…' : 'no open alerts'" @activate="open" />
      <div v-if="unread.length" class="pad dim">
        <div v-for="s in unread" :key="s.repo">couldn't read {{ s.repo }}: {{ s.unread.join('; ') }}</div>
      </div>
    </div>
  </div>
</template>
