<script setup>
// Issues across GitHub repos and GitLab projects: filter by state, label, mine and age; open, close,
// reopen and comment; n opens a new issue in any repo.
import { computed, reactive, ref, watch } from 'vue'
import { get, log, qs } from '../api.js'
import { act, useLoad } from '../load.js'
import { useKeys } from '../keys.js'
import { loadGroups, scope } from '../scope.js'
import { ago, ask } from '../ui.js'
import DataTable from '../components/DataTable.vue'
import Scope from '../components/Scope.vue'
import TextPane from '../components/TextPane.vue'

const STATES = { open: 'open', closed: 'closed', all: 'all' }
const f = reactive({ state: 'open', mine: false, label: '', stale: 0 })
function issuesUrl(q) {
  const extra = qs({ state: f.state, mine: f.mine, label: f.label, stale: f.stale || '' }).slice(1)
  return `/issues${q || (extra ? '?' : '')}${q && extra ? '&' : ''}${extra}`
}
const { data, loading, load } = useLoad((q) => get(issuesUrl(q)))
watch(f, () => load())
loadGroups()
const sel = ref(null)
const detail = ref('')
const keyOf = (i) => `${i.repo}#${i.number}`
const rows = computed(() => (data.value?.issues || []).map((i) => ({
  ...i, tags: i.labels.join(', '), who: i.assignees.map((a) => `@${a}`).join(' '), when: ago(i.updated),
})))
const columns = [
  { key: 'repo', label: 'repo', cls: (r) => (r.provider === 'gitlab' ? 'warn' : '') },
  { key: 'number', label: '#', right: true, fmt: (v) => `#${v}` },
  { key: 'title', label: 'title', cls: () => '' },
  { key: 'tags', label: 'labels', cls: () => 'accent' },
  { key: 'who', label: 'assigned', cls: () => 'dim' },
  { key: 'state', label: 'state', cls: (r) => (r.state === 'open' ? 'ok' : 'dim') },
  { key: 'when', label: 'updated', cls: () => 'dim' },
]
const current = computed(() => rows.value.find((i) => keyOf(i) === sel.value))

async function show() {
  const i = current.value
  if (!i || form.open) return
  detail.value = '…'
  try { detail.value = (await get(`/issue${qs({ repo: i.repo, number: i.number })}`)).text } catch (e) { detail.value = `error: ${e.message}` }
}
watch(sel, show)

async function setState(close) {
  const i = current.value
  if (!i || (close ? i.state !== 'open' : i.state === 'open')) return
  const text = await act(`${close ? 'Close' : 'Reopen'} ${keyOf(i)} ${i.title}?`, '/issue', { repo: i.repo, number: i.number, action: close ? 'close' : 'reopen' })
  if (text !== null) { detail.value = text; load(true) }
}
async function comment() {
  const i = current.value
  if (!i) return
  const said = await ask(`Comment on ${keyOf(i)}`, { input: { placeholder: 'your comment' }, allowDry: false })
  if (!said?.trim()) return
  const text = await act(`Post the comment on ${keyOf(i)}?`, '/issue', { repo: i.repo, number: i.number, action: 'comment', text: said }, { detail: said })
  if (text !== null) { detail.value = text; show() }
}

// the new-issue form
const form = reactive({ open: false, repo: '', title: '', body: '', labels: '', assignees: '' })
const repoNames = ref([])
async function newIssue() {
  form.open = true
  if (!form.repo && current.value) form.repo = current.value.repo
  if (!repoNames.value.length) {
    try { repoNames.value = (await get('/repos')).repos.map((r) => r.repo) } catch (e) { log(e.message, 'error') }
  }
}
const split = (s) => s.split(',').map((x) => x.trim()).filter(Boolean)
async function submit() {
  if (!form.repo || !form.title.trim()) { log('a repo and a title, please', 'warn'); return }
  const text = await act(`Open an issue in ${form.repo}: ${form.title}?`, '/issues', {
    repo: form.repo, title: form.title, body: form.body, labels: split(form.labels), assignees: split(form.assignees),
  })
  if (text === null) return
  detail.value = text
  if (!text.startsWith('error')) { Object.assign(form, { open: false, title: '', body: '', labels: '', assignees: '' }); load(true) }
}
const open = () => current.value && window.open(current.value.url, '_blank', 'noopener')
const cycle = () => { const k = Object.keys(STATES); f.state = k[(k.indexOf(f.state) + 1) % k.length] }
useKeys({ r: () => load(true), n: newIssue, c: () => setState(true), u: () => setState(false), m: comment, o: open, v: cycle, i: () => { f.mine = !f.mine } })
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <Scope :groups="scope.groups" />
      <span class="row"><button v-for="(l, k) in STATES" :key="k" class="chip" :class="{ on: f.state === k }" @click="f.state = k">{{ l }}</button></span>
      <button class="chip" :class="{ on: f.mine }" @click="f.mine = !f.mine"><span class="k">i</span> mine</button>
      <select v-model="f.label" aria-label="label"><option value="">any label</option><option v-for="l in data?.labels || []" :key="l" :value="l">{{ l }}</option></select>
      <select v-model.number="f.stale" aria-label="age"><option :value="0">any age</option><option :value="30">untouched 30d</option><option :value="90">untouched 90d</option></select>
      <span class="spacer" />
      <button class="primary" @click="newIssue"><span class="k">n</span> new</button>
      <button :disabled="!current" @click="comment"><span class="k">m</span> comment</button>
      <button :disabled="current?.state !== 'open'" @click="setState(true)"><span class="k">c</span> close</button>
      <button :disabled="!current || current.state === 'open'" @click="setState(false)"><span class="k">u</span> reopen</button>
      <button :disabled="!current" @click="open"><span class="k">o</span> open</button>
    </div>
    <div class="split">
      <div><DataTable v-model="sel" :columns="columns" :rows="rows" :row-key="keyOf" :empty="loading ? 'loading…' : 'no issues'" @activate="open" /></div>
      <div class="pad">
        <div v-if="form.open" class="box">
          <div class="form">
            <label>repo</label>
            <input v-model="form.repo" list="issue-repos" placeholder="owner/name or gitlab:group/project">
            <label>title</label><input v-model="form.title" placeholder="what's wrong, or what's wanted" @keydown.enter="submit">
            <label>labels</label><input v-model="form.labels" placeholder="bug, docs">
            <label>assignees</label><input v-model="form.assignees" placeholder="login, login (GitHub)">
          </div>
          <datalist id="issue-repos"><option v-for="r in repoNames" :key="r" :value="r" /></datalist>
          <textarea v-model="form.body" rows="10" placeholder="details (Markdown)" style="margin-top: 8px" />
          <div class="row" style="margin-top: 8px">
            <button class="primary" @click="submit">open issue</button>
            <button @click="form.open = false; show()">cancel</button>
          </div>
        </div>
        <TextPane v-else :text="detail" placeholder="pick an issue; n opens a new one" />
      </div>
    </div>
  </div>
</template>
