<script setup>
// The console: type mtr commands as in a terminal. ↑/↓ walk the history; changes ask first.
import { nextTick, onMounted, ref } from 'vue'
import { get, log, post, state } from '../api.js'
import { ask } from '../ui.js'
import TextPane from '../components/TextPane.vue'

const HISTORY = 'mtr.console.history'
const line = ref('')
const out = ref([]) // [{line, text}]
const writes = ref(new Set())
const history = ref(load())
const at = ref(-1)
const input = ref(null)
const screen = ref(null)
const running = ref(false)

function load() { try { return JSON.parse(localStorage.getItem(HISTORY) || '[]') } catch { return [] } }
function remember(l) {
  history.value = [l, ...history.value.filter((x) => x !== l)].slice(0, 100)
  try { localStorage.setItem(HISTORY, JSON.stringify(history.value)) } catch { /* private window */ }
}
const QUICK = ['status', 'prs', 'runs', 'approve --dry-run', 'releases --problems', 'security', 'doctor', 'stats']
function quick(l) { line.value = l; run() }
onMounted(async () => {
  if (!window.matchMedia('(pointer: coarse)').matches) input.value?.focus() // no keyboard popping up on phones
  try {
    const c = await get('/console/commands')
    writes.value = new Set(c.writes)
    out.value.push({ line: '', text: `${c.help}\nhere: ${[...c.reads, ...c.writes, ...c.plugins].sort().join(' ')}` })
  } catch (e) { log(e.message, 'error') }
})
async function run() {
  let l = line.value.trim()
  if (!l || running.value) return
  if (l === 'clear') { out.value = []; line.value = ''; return }
  remember(l)
  at.value = -1
  line.value = ''
  const words = l.replace(/^(mtr|mortar)\s+/, '').split(/\s+/)
  if (writes.value.has(words[0]) && !words.includes('--dry-run')) {
    const a = await ask(`Run "${l}"? It can change repos.`)
    if (!a) { out.value.push({ line: l, text: 'cancelled' }); return }
    if (a === 'dry') l += ' --dry-run'
  }
  running.value = true
  try {
    out.value.push({ line: l, text: (await post('/console', { line: l })).text })
  } catch (e) {
    out.value.push({ line: l, text: `error: ${e.message}` })
  } finally {
    running.value = false
    await nextTick()
    screen.value?.scrollTo({ top: screen.value.scrollHeight })
    input.value?.focus()
  }
}
function key(e) {
  if (e.key === 'ArrowUp' && at.value < history.value.length - 1) { at.value++; line.value = history.value[at.value]; e.preventDefault() }
  else if (e.key === 'ArrowDown') { at.value = Math.max(-1, at.value - 1); line.value = at.value < 0 ? '' : history.value[at.value]; e.preventDefault() }
  else if (e.key === 'l' && e.ctrlKey) { out.value = []; e.preventDefault() }
}
</script>

<template>
  <div class="view" @click.self="input?.focus()">
    <div ref="screen" class="scroll pad console-out">
      <div v-for="(o, i) in out" :key="i" style="margin-bottom: 10px">
        <div v-if="o.line"><span class="accent">$ mtr</span> {{ o.line.replace(/^(mtr|mortar)\s+/, '') }}</div>
        <TextPane :text="o.text" />
      </div>
      <div v-if="running" class="warn cursor">running </div>
    </div>
    <div class="toolbar quick" aria-label="quick commands">
      <button v-for="q in QUICK" :key="q" :disabled="running" @click="quick(q)">{{ q }}</button>
    </div>
    <div class="toolbar" style="border-top: 1px solid var(--line); border-bottom: 0">
      <span class="accent">$ mtr</span>
      <input ref="input" v-model="line" class="grow" :disabled="running" aria-label="command"
             placeholder="status · prs · runs · doctor --group web · stats --days 90 · clear" @keydown.enter="run" @keydown="key">
      <span v-if="state.dryRun" class="warn">dry run</span>
    </div>
  </div>
</template>
