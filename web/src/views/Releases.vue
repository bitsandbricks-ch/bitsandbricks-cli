<script setup>
// Each repo's latest release, its assets, missing assets and the last release run.
// Enter opens the repo's releases as a page; n creates a release.
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { get, log } from '../api.js'
import { useLoad } from '../load.js'
import { useKeys } from '../keys.js'
import { loadGroups, scope } from '../scope.js'
import DataTable from '../components/DataTable.vue'
import ReleaseForm from '../components/ReleaseForm.vue'
import Scope from '../components/Scope.vue'
import TextPane from '../components/TextPane.vue'

const router = useRouter()
const problems = ref(false)
const { data, loading, load } = useLoad((q) => `/releases${q}${problems.value ? (q ? '&' : '?') + 'problems=1' : ''}`)
loadGroups()
const sel = ref(null)
const form = ref(false)
const output = ref('')
const repoNames = ref([])
const rows = computed(() => (data.value?.releases || []).map((r) => ({ ...r, issues: r.problems.join('; ') || 'ok' })))
const columns = [
  { key: 'repo', label: 'repo', cls: (r) => (r.provider === 'gitlab' ? 'warn' : '') },
  { key: 'release', label: 'release', cls: () => '' }, { key: 'when', label: 'when', cls: () => 'dim' },
  { key: 'assets', label: 'assets' }, { key: 'run', label: 'release run' }, { key: 'issues', label: 'problems' },
]
const current = computed(() => rows.value.find((r) => r.repo === sel.value))
const toggle = () => { problems.value = !problems.value; load() }
const page = (r) => { const repo = r?.repo || sel.value; if (repo) router.push({ path: '/release', query: { repo } }) }
async function newRelease() {
  form.value = true
  output.value = ''
  if (!repoNames.value.length) {
    try { repoNames.value = (await get('/repos')).repos.map((r) => r.repo) } catch (e) { log(e.message, 'error') }
  }
}
function done({ text, ok }) { output.value = text; if (ok) { form.value = false; load(true) } }
useKeys({ r: () => load(true), p: toggle, n: newRelease, Escape: () => { form.value = false },
  o: () => current.value?.url && window.open(current.value.url, '_blank', 'noopener') })
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <Scope :groups="scope.groups" />
      <label class="check"><input :checked="problems" type="checkbox" @change="toggle"> only problems <span class="dim">(p)</span></label>
      <span class="spacer" />
      <button class="primary" @click="newRelease"><span class="k">n</span> new release</button>
      <button :disabled="!sel" @click="page()"><span class="k">⏎</span> releases of the repo</button>
    </div>
    <div class="split">
      <div><DataTable v-model="sel" :columns="columns" :rows="rows" :row-key="(r) => r.repo" :empty="loading ? 'loading…' : 'no releases'" @activate="page" /></div>
      <div class="pad">
        <ReleaseForm v-if="form" :repo="sel || ''" :repos="repoNames" @done="done" @cancel="form = false" />
        <template v-if="output"><h2>output</h2><TextPane :text="output" /></template>
        <TextPane v-if="!form" :text="current?.text" placeholder="pick a repo; Enter shows all its releases" />
      </div>
    </div>
  </div>
</template>
