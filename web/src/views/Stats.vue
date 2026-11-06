<script setup>
// Stats: what moved over the last N days, for all repos, per group and per repo.
import { computed, ref } from 'vue'
import { useLoad } from '../load.js'
import { useKeys } from '../keys.js'
import { loadGroups, scope } from '../scope.js'
import Bars from '../components/Bars.vue'
import DataTable from '../components/DataTable.vue'
import Scope from '../components/Scope.vue'

const days = ref(30)
const { data, loading, load } = useLoad((q) => `/metrics${q}${q ? '&' : '?'}days=${days.value}`)
loadGroups()
const sel = ref(null)
const t = computed(() => data.value?.totals)
const n = (v, u = '') => (v === null || v === undefined ? '-' : `${v}${u}`)
const kpis = computed(() => t.value ? [
  ['PRs merged', t.value.merged], ['PRs opened', t.value.opened], ['median time to merge', n(t.value.median_merge_hours, 'h')],
  ['open PRs', t.value.open], [`stale (>14d)`, t.value.stale], ['runs', t.value.runs],
  ['CI green', n(t.value.success_rate, '%')], ['failed runs', t.value.failed_runs], ['releases', t.value.releases],
] : [])
const rows = computed(() => (data.value?.repos || []).filter((r) => r.merged || r.open || r.runs || r.releases))
const columns = [
  { key: 'repo', label: 'repo', cls: (r) => (r.provider === 'gitlab' ? 'warn' : '') },
  { key: 'merged', label: 'merged', right: true, cls: () => '' }, { key: 'opened', label: 'opened', right: true, cls: () => '' },
  { key: 'median_merge_hours', label: 'merge time', right: true, fmt: (v) => n(v, 'h'), cls: () => '' },
  { key: 'open', label: 'open', right: true, cls: () => '' },
  { key: 'stale', label: 'stale', right: true, cls: (r) => (r.stale ? 'warn' : 'dim') },
  { key: 'runs', label: 'runs', right: true, cls: () => '' },
  { key: 'success_rate', label: 'green', right: true, fmt: (v) => n(v, '%'), cls: (r) => (r.success_rate === null ? 'dim' : r.success_rate >= 90 ? 'ok' : r.success_rate >= 70 ? 'warn' : 'bad') },
  { key: 'median_run_minutes', label: 'run time', right: true, fmt: (v) => n(v, 'm'), cls: () => '' },
  { key: 'releases', label: 'releases', right: true, cls: () => '' },
  { key: 'last_release_days', label: 'last release', right: true, fmt: (v) => (v === null ? '-' : `${v}d ago`), cls: () => 'dim' },
]
const groups = computed(() => Object.entries(data.value?.groups || {}))
useKeys({ r: () => load(true) })
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <Scope :groups="scope.groups" />
      <label class="row">last <select v-model.number="days" @change="load()"><option v-for="d in [7, 14, 30, 90, 180, 365]" :key="d" :value="d">{{ d }} days</option></select></label>
      <span v-if="loading" class="warn cursor">counting </span>
      <span class="spacer" />
      <span v-if="data" class="dim" title="answers from the cache and 304s cost no rate limit">cache: {{ data.cache.github.cache_hits || 0 }} hits, {{ data.cache.github.not_modified || 0 }} not modified, {{ data.cache.github.requests || 0 }} requests</span>
    </div>
    <div class="scroll pad">
      <div class="kpis"><div v-for="[l, v] in kpis" :key="l" class="kpi"><div class="v">{{ v }}</div><div class="l">{{ l }}</div></div></div>
      <div v-if="t" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px">
        <Bars title="PRs merged per week" :labels="t.weeks" :values="t.merged_by_week" />
        <Bars title="runs per week (red: failed)" :labels="t.weeks" :values="t.runs_by_week" :bad="t.failed_by_week" />
      </div>
      <h2 v-if="groups.length > 1">per group</h2>
      <table v-if="groups.length > 1" class="table" style="margin-bottom: 14px">
        <thead><tr><th>group</th><th class="right">repos</th><th class="right">merged</th><th class="right">merge time</th><th class="right">open</th><th class="right">green</th><th class="right">releases</th></tr></thead>
        <tbody><tr v-for="[g, v] in groups" :key="g"><td>{{ g }}</td><td class="right">{{ v.repos }}</td><td class="right">{{ v.merged }}</td><td class="right">{{ n(v.median_merge_hours, 'h') }}</td><td class="right">{{ v.open }}</td><td class="right">{{ n(v.success_rate, '%') }}</td><td class="right">{{ v.releases }}</td></tr></tbody>
      </table>
      <h2>per repo</h2>
      <DataTable v-model="sel" :columns="columns" :rows="rows" :row-key="(r) => r.repo" :empty="loading ? 'loading…' : 'nothing moved'" />
    </div>
  </div>
</template>
