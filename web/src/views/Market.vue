<script setup>
// The marketplace: the plugin index and its signature. Installing changes the whole server, so it
// happens where Mason runs (pip install), never from the web.
import { onMounted, ref } from 'vue'
import { get, log } from '../api.js'

const idx = ref(null)
const error = ref('')
async function load() {
  error.value = ''
  try { idx.value = await get('/market') } catch (e) { error.value = e.message; log(e.message, 'error') }
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
        <div class="dim">install with <span class="accent">pip install</span> next to Mason (then restart it), or <span class="accent">mason market install NAME</span> on your machine; once installed, each user switches it on or off under plugins.</div>
      </div>
      <div v-for="p in idx.plugins" :key="p.package" class="box">
        <div class="row">
          <span class="accent">{{ p.name }}</span><span class="dim">{{ p.package }} {{ p.version }}</span>
          <span v-if="p.license" class="chip">{{ p.license }}</span>
          <span v-if="p.web" class="chip on">web</span>
          <span class="spacer" />
          <span v-if="p.installed" class="ok">installed {{ p.installed }}{{ p.loaded ? '' : ' (restart to load)' }}</span>
        </div>
        <div>{{ p.description }}</div>
        <div class="dim">provides: {{ p.provides.join(', ') || '-' }}<template v-if="p.homepage"> · <a :href="p.homepage" target="_blank" rel="noopener noreferrer">{{ p.homepage }}</a></template></div>
      </div>
    </div>
  </div>
</template>
