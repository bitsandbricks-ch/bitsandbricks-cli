<script setup>
// Checks: the repos against the defaults (bnb doctor), fixing drift, syncing files, plus plugin checks.
import { computed, onMounted, ref } from 'vue'
import { get, log, post } from '../api.js'
import { act } from '../load.js'
import { useKeys } from '../keys.js'
import { loadGroups, scope } from '../scope.js'
import TextPane from '../components/TextPane.vue'

const output = ref('')
const running = ref('')
const only = ref('')
const pluginChecks = ref([])
loadGroups()
onMounted(async () => {
  try {
    pluginChecks.value = (await get('/plugins')).plugins.filter((p) => p.on).flatMap((p) => p.checks.map((c) => ({ ...c, plugin: p.name })))
  } catch { /* none */ }
})
const repos = computed(() => only.value.split(/[\s,]+/).filter(Boolean))
const where = computed(() => (repos.value.length ? repos.value.join(', ') : scope.group ? `group ${scope.group}` : 'every repo'))

async function doctor() {
  running.value = 'doctor'
  output.value = ''
  try { output.value = (await post('/doctor', { repos: repos.value, group: scope.group || undefined })).text || 'ok' } catch (e) { output.value = `error: ${e.message}`; log(e.message, 'error') }
  running.value = ''
}
async function fix() {
  const t = await act(`Apply the defaults to ${where.value} (settings, security, rulesets, protected branches)?`, '/doctor', { repos: repos.value, group: scope.group || undefined, fix: true })
  if (t !== null) output.value = t
}
async function sync() {
  const t = await act(`Open PRs that sync the shared files into ${where.value}?`, '/sync', { repos: repos.value, group: scope.group || undefined })
  if (t !== null) output.value = t
}
async function plugin(c) {
  running.value = c.key
  try { output.value = (await post(`/plugins/${c.plugin}/checks/${c.key}`)).text } catch (e) { output.value = `error: ${e.message}` }
  running.value = ''
}
useKeys({ c: doctor, f: fix, s: sync })
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <select v-model="scope.group" aria-label="group"><option value="">all groups</option><option v-for="g in scope.groups" :key="g" :value="g">{{ g }}</option></select>
      <input v-model="only" placeholder="repos (empty: all), e.g. web api gitlab:grp/x" style="min-width: 28ch">
      <button class="primary" :disabled="!!running" @click="doctor"><span class="k">c</span> doctor</button>
      <button :disabled="!!running" @click="fix"><span class="k">f</span> fix drift</button>
      <button :disabled="!!running" @click="sync"><span class="k">s</span> sync files</button>
      <span class="spacer" />
      <button v-for="c in pluginChecks" :key="c.plugin + c.key" :disabled="!!running" @click="plugin(c)">{{ c.label }} <span class="dim">[{{ c.plugin }}]</span></button>
    </div>
    <div class="scroll pad">
      <div v-if="running" class="warn cursor">running {{ running }} </div>
      <TextPane :text="output" placeholder="c checks the repos against the defaults: settings, security, release immutability, rulesets (GitHub), protected branches (GitLab) and files. f fixes what it can, s opens PRs with the files." />
    </div>
  </div>
</template>
