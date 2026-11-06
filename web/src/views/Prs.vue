<script setup>
// Pull and merge requests: all open ones, with checks, review, conflicts and auto-merge.
import { computed, ref, watch } from 'vue'
import { get, qs } from '../api.js'
import { act, useLoad } from '../load.js'
import { useKeys } from '../keys.js'
import { loadGroups, scope } from '../scope.js'
import DataTable from '../components/DataTable.vue'
import Scope from '../components/Scope.vue'
import TextPane from '../components/TextPane.vue'

const { data, loading, load } = useLoad((q) => get(`/prs${q}`))
loadGroups()
const filter = ref('all')
const sel = ref(null)
const detail = ref('')
const diff = ref(false)
const FILTERS = { all: 'all open', ready: 'ready', drafts: 'drafts', auto: 'auto-merge', conflicts: 'conflicts', merged: 'merged' }

const keyOf = (p) => `${p.repo}#${p.number}`
const rows = computed(() => {
  if (filter.value === 'merged') return (data.value?.merged || []).map((p) => ({ ...p, state: `merged ${p.by ? 'by ' + p.by : ''}` }))
  return (data.value?.prs || []).filter((p) => ({
    all: true, ready: !p.draft && !p.conflicts, drafts: p.draft, auto: p.auto, conflicts: p.conflicts,
  })[filter.value]).map((p) => ({
    ...p, state: p.draft ? 'draft' : p.conflicts ? 'CONFLICTS' : 'ready', flags: [p.auto && 'auto-merge', p.review].filter(Boolean).join(' · '),
  }))
})
const columns = [
  { key: 'repo', label: 'repo', cls: (r) => (r.provider === 'gitlab' ? 'warn' : '') },
  { key: 'number', label: '#', right: true, fmt: (v, r) => (r.provider === 'gitlab' ? `!${v}` : `#${v}`) },
  { key: 'title', label: 'title', cls: () => '' },
  { key: 'state', label: 'state' },
  { key: 'checks', label: 'checks' },
  { key: 'flags', label: '' },
  { key: 'author', label: 'by', cls: () => 'dim' },
]
const current = computed(() => rows.value.find((p) => keyOf(p) === sel.value))

async function show() {
  const p = current.value
  if (!p) { detail.value = ''; return }
  detail.value = '…'
  try { detail.value = (await get(`/pr${qs({ repo: p.repo, number: p.number, diff: diff.value })}`)).text } catch (e) { detail.value = `error: ${e.message}` }
}
watch([sel, diff], show)

async function doMerge(now) {
  const p = current.value
  if (!p) return
  const text = await act(`${now ? 'Merge' : 'Auto-merge'} ${keyOf(p)} ${p.title}?`, '/merge', { targets: [keyOf(p)], now, ready: p.draft })
  if (text !== null) { detail.value = text; load(true) }
}
async function mergeAll() {
  const text = await act('Auto-merge every ready PR (drafts and conflicts stay open)?', '/merge', { all: true })
  if (text !== null) { detail.value = text; load(true) }
}
async function ready() {
  const p = current.value
  if (!p || !p.draft) return
  const text = await act(`Mark ${keyOf(p)} ready for review?`, '/ready', { repo: p.repo, number: p.number })
  if (text !== null) { detail.value = text; load(true) }
}
const open = () => current.value && window.open(current.value.url, '_blank', 'noopener')
const cycle = () => { const k = Object.keys(FILTERS); filter.value = k[(k.indexOf(filter.value) + 1) % k.length] }
useKeys({ r: () => load(true), m: () => doMerge(false), M: () => doMerge(true), A: mergeAll, y: ready, d: () => { diff.value = !diff.value }, o: open, v: cycle })
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <Scope :groups="scope.groups" />
      <span class="row"><button v-for="(l, k) in FILTERS" :key="k" class="chip" :class="{ on: filter === k }" @click="filter = k">{{ l }}</button></span>
      <span class="dim">(v)</span>
      <span class="spacer" />
      <button :disabled="!current || filter === 'merged'" @click="doMerge(false)"><span class="k">m</span> auto-merge</button>
      <button :disabled="!current || filter === 'merged'" @click="doMerge(true)"><span class="k">M</span> merge now</button>
      <button :disabled="!current?.draft" @click="ready"><span class="k">y</span> ready</button>
      <button :class="{ primary: diff }" @click="diff = !diff"><span class="k">d</span> diff</button>
      <button @click="mergeAll"><span class="k">A</span> auto-merge all ready</button>
    </div>
    <div class="split">
      <div><DataTable v-model="sel" :columns="columns" :rows="rows" :row-key="keyOf" :empty="loading ? 'loading…' : 'no open pull requests'" @activate="open" /></div>
      <div class="pad"><TextPane :text="detail" placeholder="pick a pull request" /></div>
    </div>
  </div>
</template>
