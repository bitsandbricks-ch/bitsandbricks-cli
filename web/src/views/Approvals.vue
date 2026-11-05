<script setup>
// What waits for you: environments you review, outside contributors' runs, GitLab deployments and manual jobs.
import { computed, ref } from 'vue'
import { get } from '../api.js'
import { act, useLoad } from '../load.js'
import { useKeys } from '../keys.js'
import { loadGroups, scope } from '../scope.js'
import { ago } from '../ui.js'
import DataTable from '../components/DataTable.vue'
import Scope from '../components/Scope.vue'
import TextPane from '../components/TextPane.vue'

const { data, loading, load } = useLoad((q) => get(`/approvals${q}`))
loadGroups()
const sel = ref(null)
const output = ref('')
const rows = computed(() => (data.value?.approvals || []).map((r) => ({ ...r, key: `${r.repo}|${r.kind}|${r.id}`, when: ago(r.created) })))
const columns = [
  { key: 'repo', label: 'repo', cls: (r) => (r.provider === 'gitlab' ? 'warn' : '') },
  { key: 'waits_for', label: 'waits for', cls: (r) => (r.blocked ? 'dim' : 'warn') },
  { key: 'blocked', label: 'you can approve', fmt: (v) => (v ? `no: ${v}` : 'yes'), cls: (r) => (r.blocked ? 'bad' : 'ok') },
  { key: 'name', label: 'run', cls: () => '' },
  { key: 'branch', label: 'branch', cls: () => '' },
  { key: 'when', label: 'since', cls: () => 'dim' },
]
const current = computed(() => rows.value.find((r) => r.key === sel.value))
async function approve(r = current.value) {
  if (!r) return
  if (r.blocked) { output.value = `you can't approve it: ${r.blocked}`; return }
  const text = await act(`Approve ${r.waits_for} for ${r.repo} ${r.branch || ''}?`, '/approve', { repo: r.repo, id: r.id, kind: r.kind })
  if (text !== null) { output.value = text; load(true) }
}
const open = (r) => r?.url && window.open(r.url, '_blank', 'noopener')
useKeys({ r: () => load(true), a: () => approve(), o: () => open(current.value) })
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <Scope :groups="scope.groups" />
      <span class="spacer" />
      <button class="primary" :disabled="!current" @click="approve()"><span class="k">a</span> approve</button>
      <button :disabled="!current" @click="open(current)"><span class="k">o</span> open</button>
    </div>
    <div class="split">
      <div><DataTable v-model="sel" :columns="columns" :rows="rows" :empty="loading ? 'loading…' : 'nothing waits for you'" @activate="approve" /></div>
      <div class="pad"><TextPane :text="output" placeholder="a approves the selected run; Enter does too" /></div>
    </div>
  </div>
</template>
