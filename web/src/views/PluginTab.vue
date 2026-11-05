<script setup>
// A plugin's tab, like in the TUI: rows on the left, the selected row's text on the right.
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { get, log } from '../api.js'
import DataTable from '../components/DataTable.vue'
import TextPane from '../components/TextPane.vue'

const route = useRoute()
const tab = ref(null)
const sel = ref(null)
const text = ref('')
const url = ref('')
const rows = computed(() => (tab.value?.rows || []).map((r) => ({ ...r })))

watch(() => [route.params.plugin, route.params.tab], async ([p, t]) => {
  if (!p) return
  tab.value = null
  sel.value = null
  try {
    const got = (await get('/plugins')).plugins.find((x) => x.name === p)
    tab.value = got?.tabs.find((x) => x.id === t) || { title: `${p}/${t} isn't there (is the plugin on?)`, rows: [] }
  } catch (e) { log(e.message, 'error') }
}, { immediate: true })
watch(sel, async (key) => {
  text.value = ''
  url.value = ''
  if (key == null) return
  try {
    const got = await get(`/plugins/${route.params.plugin}/tabs/${route.params.tab}?key=${encodeURIComponent(key)}`)
    if (sel.value === key) { text.value = got.text; url.value = got.url || '' }
  } catch (e) { text.value = `error: ${e.message}` }
})
</script>

<template>
  <div class="view">
    <div class="toolbar"><span class="accent">{{ route.params.plugin }}</span><span>{{ tab?.title }}</span>
      <span class="spacer" /><a v-if="url" :href="url" target="_blank" rel="noopener noreferrer">open ↗</a></div>
    <div class="split">
      <div class="scroll"><DataTable v-model="sel" :columns="[{ key: 'label', label: 'item' }]" :rows="rows" :row-key="(r) => r.key" /></div>
      <div class="scroll pad"><TextPane :text="text" placeholder="pick a row" /></div>
    </div>
  </div>
</template>
