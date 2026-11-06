<script setup>
// The audit log: your own changes, sign-ins and keys (nobody can read another user's).
import { computed, onMounted, ref } from 'vue'
import { get, log } from '../api.js'
import { ago } from '../ui.js'
import { useKeys } from '../keys.js'
import DataTable from '../components/DataTable.vue'
import TextPane from '../components/TextPane.vue'

const rows = ref([])
const sel = ref(null)
async function load() {
  try { rows.value = (await get('/audit?limit=500')).map((r, i) => ({ ...r, key: i })) } catch (e) { log(e.message, 'error') }
}
const picked = computed(() => rows.value.find((r) => r.key === sel.value))
const cols = [
  { key: 'at', label: 'when', fmt: (v) => ago(v) },
  { key: 'user', label: 'who' }, { key: 'via', label: 'via' }, { key: 'action', label: 'action' },
  { key: 'target', label: 'target' },
  { key: 'ok', label: 'result', fmt: (v, r) => (r.dry_run ? 'dry run' : v ? 'ok' : 'failed') },
]
onMounted(load)
useKeys({ r: load })
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <button @click="load"><span class="k">r</span> refresh</button>
    </div>
    <div class="split">
      <div class="scroll"><DataTable v-model="sel" :columns="cols" :rows="rows" empty="nothing logged yet" /></div>
      <div class="scroll pad">
        <TextPane v-if="picked" :text="`${picked.at}  ${picked.user} via ${picked.via} from ${picked.ip}\n${picked.action} ${picked.target}\n\n${picked.detail || ''}`" />
      </div>
    </div>
  </div>
</template>
