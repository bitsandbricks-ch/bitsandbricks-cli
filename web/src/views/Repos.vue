<script setup>
// Repos: the status table (mason status) and, on the right, the selected repo with what you can do with it.
// Enter opens the repo as its own page.
import { computed, ref, watch } from 'vue'
import { qs } from '../api.js'
import { act, cached, useLoad } from '../load.js'
import { useRouter } from 'vue-router'
import { useKeys } from '../keys.js'
import { loadGroups, scope } from '../scope.js'
import DataTable from '../components/DataTable.vue'
import Scope from '../components/Scope.vue'
import TextPane from '../components/TextPane.vue'

const emit = defineEmits(['rate'])
const { data, loading, load } = useLoad((q) => `/status${q}`)
loadGroups()
const sel = ref(null)
const detail = ref('')
const output = ref('')

const head = computed(() => data.value?.head || [])
const rows = computed(() => (data.value?.rows || []).map((r) => {
  const o = { repo: r.repo, provider: r.provider }
  head.value.forEach((h, i) => { o[h] = r.cells[i] })
  return o
}))
const columns = computed(() => [
  { key: 'repo', label: 'repo', cls: (r) => (r.provider === 'gitlab' ? 'warn' : '') },
  ...head.value.map((h) => ({ key: h, label: h.toLowerCase() })),
])

watch(sel, async (repo) => {
  output.value = ''
  if (!repo) return
  detail.value = '…'
  try { detail.value = (await cached(`/repo${qs({ repo })}`)).text } catch (e) { detail.value = `error: ${e.message}` }
})
watch(() => data.value, () => emit('rate'))

const run = async (q, path, body) => { const t = await act(q, path, body); if (t !== null) { output.value = t; load(true) } }
const doctor = () => sel.value && run(`Check ${sel.value} against the defaults?`, '/doctor', { repos: [sel.value] })
const fix = () => sel.value && run(`Fix the drift of ${sel.value} (settings, security, rulesets)?`, '/doctor', { repos: [sel.value], fix: true })
const sync = () => sel.value && run(`Open a PR that syncs the shared files into ${sel.value}?`, '/sync', { repos: [sel.value] })
const merge = (now) => sel.value && run(`${now ? 'Merge now' : 'Auto-merge'} the ready PRs of ${sel.value}?`, '/merge', { targets: [sel.value], now })
const router = useRouter()
const page = (r) => router.push({ path: '/repo', query: { repo: r?.repo || sel.value } })
const open = () => { const url = detail.value.split('\n')[1]; if (url?.startsWith('http')) window.open(url, '_blank', 'noopener') }
async function newRepo() {
  const { ask } = await import('../ui.js')
  const name = await ask('New repo (owner/name, or gitlab:group/name)', { input: { placeholder: 'acme/new-service' }, allowDry: false })
  if (!name) return
  const vis = await ask(`Make ${name} public? (n: private)`, { allowDry: false })
  await run(`Create ${name} the way its group wants it?`, '/new', { repo: name, public: vis === 'yes' })
}
useKeys({ l: () => sel.value && router.push({ path: '/release', query: { repo: sel.value } }), r: () => load(true), c: doctor, f: fix, s: sync, m: () => merge(false), M: () => merge(true), o: open, n: newRepo })
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <Scope :groups="scope.groups" />
      <button @click="load(true)"><span class="k">r</span> reload</button>
      <span class="spacer" />
      <button :disabled="!sel" @click="doctor"><span class="k">c</span> check</button>
      <button :disabled="!sel" @click="fix"><span class="k">f</span> fix</button>
      <button :disabled="!sel" @click="sync"><span class="k">s</span> sync files</button>
      <button :disabled="!sel" @click="merge(false)"><span class="k">m</span> auto-merge</button>
      <button :disabled="!sel" @click="merge(true)"><span class="k">M</span> merge now</button>
      <button :disabled="!sel" @click="sel && router.push({ path: '/release', query: { repo: sel } })"><span class="k">l</span> releases</button>
      <button @click="newRepo"><span class="k">n</span> new repo</button>
      <button :disabled="!sel" @click="page()"><span class="k">⏎</span> page</button>
    </div>
    <div class="split">
      <div>
        <DataTable v-model="sel" :columns="columns" :rows="rows" :row-key="(r) => r.repo"
                   :empty="loading ? 'loading…' : 'no repos: set your orgs or a defaults repo under settings'" @activate="page" />
      </div>
      <div class="pad">
        <TextPane :text="detail" placeholder="pick a repo" />
        <template v-if="output"><h2 style="margin-top: 16px">output</h2><TextPane :text="output" /></template>
      </div>
    </div>
  </div>
</template>
