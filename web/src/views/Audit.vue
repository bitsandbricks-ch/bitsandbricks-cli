<script setup>
// The audit log: every change, sign-in and key, yours (admins can see everyone's).
import { computed, onMounted, ref, watch } from 'vue'
import { get, log, state } from '../api.js'
import { ago } from '../ui.js'
import { useKeys } from '../keys.js'
import DataTable from '../components/DataTable.vue'
import TextPane from '../components/TextPane.vue'

const rows = ref([])
const all = ref(false)
const sel = ref(null)
async function load() {
  try { rows.value = (await get(`/audit?limit=500${all.value ? '&all=1' : ''}`)).map((r, i) => ({ ...r, key: i })) } catch (e) { log(e.message, 'error') }
}
const picked = computed(() => rows.value.find((r) => r.key === sel.value))
const cols = [
  { key: 'at', label: 'when', fmt: (v) => ago(v) },
  { key: 'user', label: 'who' }, { key: 'via', label: 'via' }, { key: 'action', label: 'action' },
  { key: 'target', label: 'target' },
  { key: 'ok', label: 'result', fmt: (v, r) => (r.dry_run ? 'dry run' : v ? 'ok' : 'failed') },
]
watch(all, load)
onMounted(load)
useKeys({ r: load })
</script>

<template>
  <div class="view">
    <div class="toolbar">
      <button @click="load"><span class="k">r</span> refresh</button>
      <label v-if="state.me?.user?.is_admin" class="check"><input v-model="all" type="checkbox"> everyone</label>
    </div>
    <div class="split">
      <div class="scroll"><DataTable v-model="sel" :columns="cols" :rows="rows" empty="nothing logged yet" /></div>
      <div class="scroll pad">
        <TextPane v-if="picked" :text="`${picked.at}  ${picked.user} via ${picked.via} from ${picked.ip}\n${picked.action} ${picked.target}\n\n${picked.detail || ''}`" />
      </div>
    </div>
  </div>
</template>
