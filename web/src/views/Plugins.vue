<script setup>
// Plugins: switch them on or off for yourself (admins: for everyone), run their actions, open their tabs.
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { get, log, post, put, state } from '../api.js'
import { ask } from '../ui.js'
import TextPane from '../components/TextPane.vue'

const list = ref([])
const broken = ref({})
const output = ref('')
const params = ref({}) // `${plugin}/${action}` -> {repo, name: value}

async function load() {
  try { const got = await get('/plugins'); list.value = got.plugins; broken.value = got.broken || {} } catch (e) { log(e.message, 'error') }
}
async function toggle(p, body) {
  try { const got = await put(`/plugins/${p.name}`, body); list.value = got.plugins } catch (e) { log(e.message, 'error') }
}
function form(p, a) {
  const k = `${p.name}/${a.id}`
  if (!params.value[k]) params.value[k] = { repo: '', ...Object.fromEntries((a.params || []).map((x) => [x.name, x.choices?.[0] || ''])) }
  return params.value[k]
}
async function run(p, a) {
  const f = form(p, a)
  let dry = false
  if (a.write) {
    const yes = await ask(`Run ${a.title}${a.repo ? ` on ${f.repo || '?'}` : ''}?`, { detail: a.help || '' })
    if (!yes) return
    dry = yes === 'dry'
  }
  const { repo, ...rest } = f
  output.value = `running ${a.title}...`
  try {
    output.value = (await post(`/plugins/${p.name}/actions/${a.id}`, { repo, params: rest, dry_run: dry })).text
  } catch (e) { output.value = `error: ${e.message}` }
}
onMounted(load)
</script>

<template>
  <div class="view">
    <div class="split">
      <div class="scroll pad">
        <div v-if="!list.length" class="empty">no plugins installed: see the <RouterLink to="/market">market</RouterLink></div>
        <div v-for="p in list" :key="p.name" class="box">
          <div class="row">
            <span :class="p.on ? 'accent' : 'dim'">{{ p.name }}</span>
            <span class="dim grow">{{ p.description }}</span>
            <label class="check"><input type="checkbox" :checked="p.on" :disabled="p.off_for_all" @change="toggle(p, { on: $event.target.checked })"> on for me</label>
            <label v-if="state.me?.user?.is_admin" class="check"><input type="checkbox" :checked="!p.off_for_all" @change="toggle(p, { everyone: $event.target.checked })"> for everyone</label>
          </div>
          <div v-if="p.off_for_all" class="warn">an admin switched this plugin off</div>
          <div v-if="p.on" class="row dim" style="margin-top: 6px">
            <span v-for="t in p.tabs" :key="t.id"><RouterLink :to="`/plugins/${p.name}/${t.id}`">tab: {{ t.title }}</RouterLink></span>
            <span v-for="c in p.checks" :key="c.key" class="chip">check: {{ c.label }}</span>
            <span v-for="c in p.columns" :key="c" class="chip">column: {{ c }}</span>
            <span v-for="c in p.commands" :key="c" class="chip">console: mtr {{ c }}</span>
          </div>
          <div v-for="a in p.actions" :key="a.id" class="row" style="margin-top: 8px">
            <button :class="a.write ? 'primary' : ''" @click="run(p, a)">{{ a.title }}</button>
            <input v-if="a.repo" v-model="form(p, a).repo" placeholder="owner/repo" aria-label="repo">
            <template v-for="x in a.params || []" :key="x.name">
              <select v-if="x.choices" v-model="form(p, a)[x.name]" :aria-label="x.name"><option v-for="c in x.choices" :key="c">{{ c }}</option></select>
              <input v-else v-model="form(p, a)[x.name]" :placeholder="x.label || x.name" :aria-label="x.name">
            </template>
            <span class="dim">{{ a.help }}</span>
          </div>
        </div>
        <div v-for="(err, name) in broken" :key="name" class="box bad">{{ name }} failed to load: {{ err }}</div>
      </div>
      <div class="scroll pad"><TextPane :text="output" placeholder="what an action prints shows here" /></div>
    </div>
  </div>
</template>
