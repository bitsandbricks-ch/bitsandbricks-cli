<script setup>
// Each repo's latest release, its assets, missing assets and the last release run.
import { computed, ref } from 'vue'
import { get } from '../api.js'
import { useLoad } from '../load.js'
import { useKeys } from '../keys.js'
import { loadGroups, scope } from '../scope.js'
import DataTable from '../components/DataTable.vue'
import Scope from '../components/Scope.vue'
import TextPane from '../components/TextPane.vue'

const problems = ref(false)
const { data, loading, load } = useLoad((q) => get(`/releases${q}${problems.value ? (q ? '&' : '?') + 'problems=1' : ''}`))
loadGroups()
const sel = ref(null)
const rows = computed(() => (data.value?.releases || []).map((r) => ({ ...r, issues: r.problems.join('; ') || 'ok' })))
const columns = [
  { key: 'repo', label: 'repo', cls: (r) => (r.provider === 'gitlab' ? 'warn' : '') },
  { key: 'release', label: 'release', cls: () => '' }, { key: 'when', label: 'when', cls: () => 'dim' },
  { key: 'assets', label: 'assets' }, { key: 'run', label: 'release run' }, { key: 'issues', label: 'problems' },
]
const current = computed(() => rows.value.find((r) => r.repo === sel.value))
const toggle = () => { problems.value = !problems.value; load() }
useKeys({ r: () => load(true), p: toggle, o: () => current.value?.url && window.open(current.value.url, '_blank', 'noopener') })
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <Scope :groups="scope.groups" />
      <label class="check"><input :checked="problems" type="checkbox" @change="toggle"> only problems <span class="dim">(p)</span></label>
    </div>
    <div class="split">
      <div><DataTable v-model="sel" :columns="columns" :rows="rows" :row-key="(r) => r.repo" :empty="loading ? 'loading…' : 'no releases'" /></div>
      <div class="pad"><TextPane :text="current?.text" placeholder="pick a repo" /></div>
    </div>
  </div>
</template>
