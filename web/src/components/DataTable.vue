<script setup>
// A keyboard-driven table: ↑/↓ or j/k move, Enter activates, / filters, click a header to sort.
import { computed, nextTick, ref, watch } from 'vue'
import { stateClass } from '../ui.js'

const props = defineProps({
  columns: { type: Array, required: true }, // [{key, label, cls?: (row) => str, fmt?: (v,row) => str, right?}]
  rows: { type: Array, default: () => [] },
  rowKey: { type: Function, default: (r) => r.key ?? r.repo ?? JSON.stringify(r) },
  empty: { type: String, default: 'nothing here' },
  modelValue: { default: null }, // the selected row's key
})
const emit = defineEmits(['update:modelValue', 'activate'])
const filter = ref('')
const sortKey = ref('')
const sortDir = ref(1)
const filterInput = ref(null)
const table = ref(null)

const cell = (c, r) => (c.fmt ? c.fmt(r[c.key], r) : r[c.key] ?? '')
const shown = computed(() => {
  const f = filter.value.trim().toLowerCase()
  let out = f ? props.rows.filter((r) => props.columns.some((c) => String(cell(c, r)).toLowerCase().includes(f)))
    : props.rows.slice()
  if (sortKey.value) {
    const c = props.columns.find((x) => x.key === sortKey.value)
    out.sort((a, b) => String(cell(c, a)).localeCompare(String(cell(c, b)), undefined, { numeric: true }) * sortDir.value)
  }
  return out
})
const index = computed(() => shown.value.findIndex((r) => props.rowKey(r) === props.modelValue))

function select(i, scroll = true) {
  const r = shown.value[Math.max(0, Math.min(i, shown.value.length - 1))]
  if (!r) return
  emit('update:modelValue', props.rowKey(r))
  if (scroll) nextTick(() => table.value?.querySelector('tr.sel')?.scrollIntoView({ block: 'nearest' }))
}
function key(e) {
  if (e.target.tagName === 'INPUT') return
  const k = e.key
  if (k === 'ArrowDown' || k === 'j') { select(index.value + 1); e.preventDefault() }
  else if (k === 'ArrowUp' || k === 'k') { select(index.value - 1); e.preventDefault() }
  else if (k === 'Home' || k === 'g') { select(0); e.preventDefault() }
  else if (k === 'End' || k === 'G') { select(shown.value.length - 1); e.preventDefault() }
  else if (k === 'Enter') { const r = shown.value[index.value]; if (r) emit('activate', r); e.preventDefault() }
  else if (k === '/') { filterInput.value?.focus(); e.preventDefault() }
}
function sort(c) {
  if (sortKey.value === c.key) sortDir.value = -sortDir.value
  else { sortKey.value = c.key; sortDir.value = 1 }
}
watch(() => props.rows, () => { if (index.value < 0 && shown.value.length && props.modelValue == null) select(0, false) })
defineExpose({ focus: () => table.value?.focus(), shown })
</script>

<template>
  <div>
    <div class="row pad" style="padding-bottom: 4px">
      <span class="dim">/</span>
      <input ref="filterInput" v-model="filter" class="grow" placeholder="filter" aria-label="filter the list"
             @keydown.esc="filter = ''; table?.focus()" @keydown.enter="table?.focus()">
      <span class="dim nowrap">{{ shown.length }}<template v-if="shown.length !== rows.length">/{{ rows.length }}</template></span>
    </div>
    <table ref="table" class="table" tabindex="0" @keydown="key">
      <thead>
        <tr>
          <th v-for="c in columns" :key="c.key" :class="{ right: c.right }" @click="sort(c)">
            {{ c.label }}<span v-if="sortKey === c.key">{{ sortDir > 0 ? ' ↑' : ' ↓' }}</span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in shown" :key="rowKey(r)" :class="{ sel: rowKey(r) === modelValue }"
            @click="emit('update:modelValue', rowKey(r))" @dblclick="emit('activate', r)">
          <td v-for="c in columns" :key="c.key" :class="[c.cls ? c.cls(r) : stateClass(cell(c, r)), { right: c.right }]"
              :title="String(cell(c, r))">{{ cell(c, r) }}</td>
        </tr>
      </tbody>
    </table>
    <div v-if="!shown.length" class="empty">{{ rows.length ? 'nothing matches the filter' : empty }}</div>
  </div>
</template>
