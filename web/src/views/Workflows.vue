<script setup>
// Workflow hardening: unpinned actions, write-all tokens, pull_request_target checkouts and script
// injection in .github/workflows. p opens a PR that pins the selected repo's actions to commit SHAs.
import { computed, ref } from 'vue'
import { act, useLoad } from '../load.js'
import { useKeys } from '../keys.js'
import { loadGroups, scope } from '../scope.js'
import DataTable from '../components/DataTable.vue'
import Scope from '../components/Scope.vue'
import TextPane from '../components/TextPane.vue'

const KIND = { pinned: 'unpinned', permissions: 'token', pull_request_target: 'pr target', injection: 'injection' }
const BAD = new Set(['pull_request_target', 'injection'])
const { data, loading, load } = useLoad((q) => `/workflows${q}`)
loadGroups()
const sel = ref(null)
const output = ref('')
const rows = computed(() => (data.value?.workflows || []).flatMap((w) => w.findings.map((f, i) => ({
  ...f, repo: w.repo, kind: KIND[f.kind] || f.kind, raw: f.kind, file: `${f.path.split('/').pop()}:${f.line}`, key: `${w.repo}|${i}`,
}))))
const clean = computed(() => (data.value?.workflows || []).filter((w) => w.workflows && !w.findings.length).length)
const counts = computed(() => rows.value.reduce((m, r) => ({ ...m, [r.kind]: (m[r.kind] || 0) + 1 }), {}))
const columns = [
  { key: 'kind', label: 'kind', cls: (r) => (BAD.has(r.raw) ? 'bad' : 'warn') },
  { key: 'repo', label: 'repo', cls: () => '' },
  { key: 'file', label: 'where', cls: () => 'dim' },
  { key: 'text', label: 'finding', cls: () => '' },
]
const current = computed(() => rows.value.find((r) => r.key === sel.value))
const pinnable = computed(() => [...new Set(rows.value.filter((r) => r.raw === 'pinned').map((r) => r.repo))])
async function pinRepos(repos) {
  if (!repos.length) { output.value = 'nothing to pin'; return }
  const text = await act(`Open ${repos.length === 1 ? 'a PR' : `${repos.length} PRs`} that pin the actions in ${repos.length === 1 ? repos[0] : `${repos.length} repos`}?`,
    '/workflows/pin', { repos })
  if (text !== null) { output.value = text; load(true) }
}
const pinOne = () => current.value && pinRepos([current.value.repo])
const open = (r) => r && window.open(`https://github.com/${r.repo}/blob/HEAD/${r.path}#L${r.line}`, '_blank', 'noopener')
useKeys({ r: () => load(true), p: pinOne, A: () => pinRepos(pinnable.value), o: () => open(current.value) })
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <Scope :groups="scope.groups" />
      <span v-for="(n, k) in counts" :key="k" class="chip">{{ k }} {{ n }}</span>
      <span v-if="clean" class="chip ok">clean {{ clean }}</span>
      <span class="spacer" />
      <button class="primary" :disabled="!current || current.raw !== 'pinned'" @click="pinOne"><span class="k">p</span> pin repo</button>
      <button :disabled="!pinnable.length" @click="pinRepos(pinnable)"><span class="k">A</span> pin all</button>
      <button :disabled="!current" @click="open(current)"><span class="k">o</span> open</button>
    </div>
    <div class="split">
      <div><DataTable v-model="sel" :columns="columns" :rows="rows" :empty="loading ? 'loading…' : 'no risky workflows'" @activate="open" /></div>
      <div class="pad"><TextPane :text="output || (current ? `${current.repo}\n${current.path}:${current.line}\n\n${current.text}` : '')"
        placeholder="p opens a PR that pins the selected repo's actions to commit SHAs; A does it for every repo listed" /></div>
    </div>
  </div>
</template>
