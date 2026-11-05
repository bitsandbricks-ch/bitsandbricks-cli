<script setup>
// Workflow runs and pipelines: the newest per repo, or only the failed ones.
import { computed, ref } from 'vue'
import { get } from '../api.js'
import { useLoad } from '../load.js'
import { useKeys } from '../keys.js'
import { loadGroups, scope } from '../scope.js'
import { ago } from '../ui.js'
import DataTable from '../components/DataTable.vue'
import Scope from '../components/Scope.vue'

const failed = ref(false)
const limit = ref(3)
const { data, loading, load } = useLoad((q) => get(`/runs${q}${q ? '&' : '?'}limit=${limit.value}${failed.value ? '&failed=1' : ''}`))
loadGroups()
const sel = ref(null)
const rows = computed(() => (data.value?.runs || []).map((r) => ({ ...r, key: `${r.repo}|${r.id ?? r.error}`, when: ago(r.created) })))
const columns = [
  { key: 'repo', label: 'repo', cls: (r) => (r.provider === 'gitlab' ? 'warn' : '') },
  { key: 'state', label: 'state', fmt: (v, r) => r.error || v },
  { key: 'name', label: 'workflow', cls: () => '' },
  { key: 'event', label: 'event', cls: () => 'dim' },
  { key: 'branch', label: 'branch', cls: () => '' },
  { key: 'when', label: 'when', cls: () => 'dim' },
]
const open = (r) => r?.url && window.open(r.url, '_blank', 'noopener')
const current = computed(() => rows.value.find((r) => r.key === sel.value))
const toggle = () => { failed.value = !failed.value; load() }
useKeys({ r: () => load(true), f: toggle, o: () => open(current.value) })
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <Scope :groups="scope.groups" />
      <label class="check"><input :checked="failed" type="checkbox" @change="toggle"> only failed <span class="dim">(f)</span></label>
      <label class="row">per repo <select v-model.number="limit" @change="load()"><option v-for="n in [1, 3, 5, 10, 20]" :key="n">{{ n }}</option></select></label>
      <span class="spacer" />
      <button :disabled="!current" @click="open(current)"><span class="k">o</span> open</button>
    </div>
    <div class="scroll"><DataTable v-model="sel" :columns="columns" :rows="rows" :empty="loading ? 'loading…' : 'no runs'" @activate="open" /></div>
  </div>
</template>
