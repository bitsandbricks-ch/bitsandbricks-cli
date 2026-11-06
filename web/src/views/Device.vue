<script setup>
// Approve a device login: `mtr login https://mortar` shows a code; this page turns it into an API key.
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get, log, post } from '../api.js'

const route = useRoute()
const code = ref(String(route.query.code || ''))
const info = ref(null)
const scope = ref('write')
const done = ref('')
const error = ref('')
async function lookup() {
  error.value = ''
  info.value = null
  if (!code.value.trim()) return
  try {
    info.value = await get(`/device/info?code=${encodeURIComponent(code.value.trim())}`)
    scope.value = info.value.scope
  } catch (e) { error.value = e.message }
}
async function decide(deny) {
  try {
    await post('/device/approve', { user_code: info.value.user_code, scope: scope.value, deny })
    done.value = deny ? 'denied: the terminal gets no key' : `approved: ${info.value.client} now has a ${scope.value} key. Go back to the terminal.`
    log(done.value, deny ? 'warn' : 'ok')
  } catch (e) { error.value = e.message }
}
onMounted(lookup)
</script>

<template>
  <div class="view">
    <div class="scroll pad" style="max-width: 70ch">
      <div class="box">
        <div class="accent">device login</div>
        <p class="dim">Type the code your terminal shows after <span class="accent">mtr login</span>. Only approve a code you started yourself.</p>
        <div class="row"><input v-model="code" placeholder="ABCD-EFGH" aria-label="code" style="text-transform: uppercase" @keydown.enter="lookup"><button @click="lookup">look up</button></div>
      </div>
      <div v-if="error" class="bad">{{ error }}</div>
      <div v-if="done" class="box ok">{{ done }}</div>
      <div v-else-if="info" class="box">
        <div><span class="accent">{{ info.client }}</span> asks for an API key ({{ info.user_code }}).</div>
        <div class="row" style="margin-top: 8px">
          <label class="check"><input v-model="scope" type="radio" value="read"> read only</label>
          <label class="check"><input v-model="scope" type="radio" value="write"> read and change repos</label>
        </div>
        <div class="row" style="margin-top: 10px"><button class="primary" @click="decide(false)">approve</button><button class="danger" @click="decide(true)">deny</button></div>
      </div>
    </div>
  </div>
</template>
