<script setup>
// The marketplace: the plugin index, its signature, and (for admins, when allowed) installing.
import { onMounted, ref } from 'vue'
import { get, log, post } from '../api.js'
import { ask } from '../ui.js'
import TextPane from '../components/TextPane.vue'

const idx = ref(null)
const output = ref('')
const error = ref('')
async function load() {
  error.value = ''
  try { idx.value = await get('/market') } catch (e) { error.value = e.message; log(e.message, 'error') }
}
async function install(p) {
  if (!(await ask(`Install ${p.name} ${p.version} on Mason?`, { allowDry: false, detail: `${p.source}\nIt runs inside Mason for every user. Restart Mason afterwards.` }))) return
  output.value = `installing ${p.name}...`
  try { output.value = (await post(`/market/${p.name}/install`)).text } catch (e) { output.value = `error: ${e.message}` }
  load()
}
onMounted(load)
</script>

<template>
  <div class="view">
    <div v-if="error" class="pad bad">{{ error }}</div>
    <div v-else-if="idx" class="scroll pad">
      <div class="box">
        <span class="accent">{{ idx.name || 'plugin index' }}</span> <span class="dim">{{ idx.index }}</span><br>
        signature: <span :class="idx.status === 'verified' ? 'ok' : 'bad'">{{ idx.status }}</span> <span class="dim">{{ idx.detail }}</span>
        <div v-if="idx.description" class="dim">{{ idx.description }}</div>
        <div v-if="!idx.can_install" class="dim">installing from here is for admins with MASON_PLUGIN_INSTALL=1; otherwise <span class="accent">pip install</span> next to Mason, or <span class="accent">mason market install NAME</span> on your machine.</div>
      </div>
      <div v-for="p in idx.plugins" :key="p.package" class="box">
        <div class="row">
          <span class="accent">{{ p.name }}</span><span class="dim">{{ p.package }} {{ p.version }}</span>
          <span v-if="p.license" class="chip">{{ p.license }}</span>
          <span v-if="p.web" class="chip on">web</span>
          <span class="spacer" />
          <span v-if="p.installed" class="ok">installed {{ p.installed }}{{ p.loaded ? '' : ' (restart to load)' }}</span>
          <button v-if="idx.can_install && idx.status === 'verified' && p.source" class="primary" @click="install(p)">{{ p.installed ? 'update' : 'install' }}</button>
        </div>
        <div>{{ p.description }}</div>
        <div class="dim">provides: {{ p.provides.join(', ') || '-' }}<template v-if="p.homepage"> · <a :href="p.homepage" target="_blank" rel="noopener noreferrer">{{ p.homepage }}</a></template></div>
      </div>
      <TextPane v-if="output" :text="output" />
    </div>
  </div>
</template>
