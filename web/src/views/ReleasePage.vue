<script setup>
// One repo's releases as a page (Enter on Releases or on a repo's page): every recent release on the
// left, the picked one in full on the right; n creates a release, Esc goes back.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { get, qs } from '../api.js'
import { cached } from '../load.js'
import { useKeys } from '../keys.js'
import DataTable from '../components/DataTable.vue'
import ReleaseForm from '../components/ReleaseForm.vue'
import TextPane from '../components/TextPane.vue'

const route = useRoute()
const router = useRouter()
const repo = computed(() => String(route.query.repo || ''))
const page = ref(null)
const text = ref('')
const sel = ref(null)
const output = ref('')
const form = ref(false)
const loading = ref(false)

async function load(tag = '', fresh = false) {
  if (!repo.value) return
  loading.value = true
  try {
    const got = await cached(`/release${qs({ repo: repo.value, tag, fresh })}`, fresh)
    if (!tag) page.value = got
    text.value = got.text
    sel.value = got.selected || null
  } catch (e) { text.value = `error: ${e.message}` } finally { loading.value = false }
}
watch(repo, () => { page.value = null; form.value = false; output.value = ''; load(String(route.query.tag || '')) }, { immediate: true })
watch(sel, (tag, before) => { if (tag && before && tag !== before) { router.replace({ query: { ...route.query, tag } }); load(tag) } })

const rows = computed(() => page.value?.releases || [])
const columns = [
  { key: 'tag', label: 'tag', cls: () => 'accent' }, { key: 'title', label: 'title', cls: () => '' },
  { key: 'state', label: 'state' }, { key: 'when', label: 'when', cls: () => 'dim' },
  { key: 'assets', label: 'assets', right: true },
]
const current = computed(() => rows.value.find((r) => r.tag === sel.value))
const open = () => { const url = current.value?.url || page.value?.url; if (url) window.open(url, '_blank', 'noopener') }
const back = () => (window.history.state?.back ? router.back() : router.push('/releases'))
function done({ text: t, tag, ok }) {
  output.value = t
  if (!ok) return
  form.value = false
  load('', true).then(() => { if (rows.value.some((r) => r.tag === tag)) sel.value = tag })
}
useKeys({ Escape: () => (form.value ? (form.value = false) : back()), Backspace: back, n: () => { form.value = true },
  o: open, r: () => load(sel.value || '', true) })
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <button @click="back"><span class="k">esc</span> back</button>
      <span class="accent">releases › {{ repo }}</span>
      <span class="spacer" />
      <button @click="load(sel || '', true)"><span class="k">r</span> reload</button>
      <button class="primary" @click="form = true"><span class="k">n</span> new release</button>
      <button :disabled="!page" @click="open"><span class="k">o</span> open</button>
      <router-link class="chip" :to="{ path: '/repo', query: { repo } }">repo page</router-link>
    </div>
    <div class="split">
      <div><DataTable v-model="sel" :columns="columns" :rows="rows" :row-key="(r) => r.tag"
                      :empty="loading ? 'loading…' : 'no releases yet: n creates the first'" @activate="open" /></div>
      <div class="pad">
        <ReleaseForm v-if="form" :repo="repo" :suggest="page?.suggest" @done="done" @cancel="form = false" />
        <template v-if="output"><h2>output</h2><TextPane :text="output" /></template>
        <TextPane v-if="!form" :text="text" :placeholder="loading ? 'loading…' : 'pick a release'" />
      </div>
    </div>
  </div>
</template>
