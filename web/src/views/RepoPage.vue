<script setup>
// One repo as a page (Enter on Repos): its whole detail, what you can do with it, and what that printed.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { qs } from '../api.js'
import { act, cached, forget } from '../load.js'
import { useKeys } from '../keys.js'
import ReleaseForm from '../components/ReleaseForm.vue'
import TextPane from '../components/TextPane.vue'

const route = useRoute()
const router = useRouter()
const repo = computed(() => String(route.query.repo || ''))
const gitlab = computed(() => repo.value.startsWith('gitlab:'))
const detail = ref('')
const output = ref('')
const form = ref('') // '', 'release' or 'tag'
const suggest = ref('')

async function load(fresh = false) {
  if (!repo.value) return
  if (!detail.value) detail.value = '…'
  try { detail.value = (await cached(`/repo${qs({ repo: repo.value, fresh })}`, fresh)).text } catch (e) { detail.value = `error: ${e.message}` }
}
watch(repo, () => { detail.value = ''; output.value = ''; form.value = ''; load() }, { immediate: true })

async function run(q, path, body) {
  const t = await act(q, path, body)
  if (t === null) return
  output.value = t
  forget('/status'); forget('/repo'); forget('/release')
  load(true)
}
const r = () => [repo.value]
const actions = computed(() => [
  { k: 'c', label: 'check against the defaults', fn: () => run(`Check ${repo.value} against the defaults?`, '/doctor', { repos: r() }) },
  { k: 'f', label: 'fix the drift', fn: () => run(`Fix the drift of ${repo.value} (settings, security, rulesets)?`, '/doctor', { repos: r(), fix: true }) },
  { k: 's', label: 'sync the shared files (PR)', fn: () => run(`Open a PR that syncs the shared files into ${repo.value}?`, '/sync', { repos: r() }) },
  { k: 'm', label: 'auto-merge its ready PRs', fn: () => run(`Auto-merge the ready PRs of ${repo.value}?`, '/merge', { targets: r() }) },
  { k: 'M', label: 'merge its ready PRs now', fn: () => run(`Merge the ready PRs of ${repo.value} now?`, '/merge', { targets: r(), now: true }) },
  { k: 'n', label: 'create a release', fn: () => newRelease('release') },
  { k: 'T', label: 'push a tag (its workflow releases)', fn: () => newRelease('tag') },
  { k: 'l', label: 'its releases', fn: () => router.push({ path: '/release', query: { repo: repo.value } }) },
  { k: 'o', label: `open on ${gitlab.value ? 'GitLab' : 'GitHub'}`, fn: open },
])
const publishes = ref('')
async function newRelease(mode) {
  form.value = mode
  if (!suggest.value) {
    try { const p = await cached(`/release${qs({ repo: repo.value })}`); suggest.value = p.suggest; publishes.value = p.publishes || '' } catch { suggest.value = 'v0.1.0' }
  }
}
function done({ text, ok }) { output.value = text; if (ok) { form.value = ''; forget('/release'); forget('/releases'); load(true) } }
const open = () => { const url = detail.value.split('\n')[1]; if (url?.startsWith('http')) window.open(url, '_blank', 'noopener') }
const back = () => (window.history.state?.back ? router.back() : router.push('/'))
const keys = { Escape: () => (form.value ? (form.value = '') : back()), Backspace: back, r: () => load(true) }
watch(actions, (list) => list.forEach((a) => { keys[a.k] = a.fn }), { immediate: true })
useKeys(keys)
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <button @click="back"><span class="k">esc</span> back</button>
      <span class="accent">repos › {{ repo }}</span>
      <span class="spacer" />
      <button @click="load(true)"><span class="k">r</span> reload</button>
    </div>
    <div class="split page">
      <div class="pad">
        <h2>actions</h2>
        <div class="actions">
          <button v-for="a in actions" :key="a.k" :class="{ primary: a.k === 'n' }" @click="a.fn"><span class="k">{{ a.k }}</span> {{ a.label }}</button>
        </div>
        <template v-if="output"><h2 style="margin-top: 16px">output</h2><TextPane :text="output" /></template>
      </div>
      <div class="pad">
        <ReleaseForm v-if="form" :key="form" :mode="form" :repo="repo" :suggest="suggest" :publishes="publishes"
                     @done="done" @cancel="form = ''" />
        <TextPane :text="detail" placeholder="loading…" />
      </div>
    </div>
  </div>
</template>
