<script setup>
// The y/N prompt every change goes through; "d" runs it as a dry run.
import { nextTick, ref, watch } from 'vue'
import { answer, prompt } from '../ui.js'
import { state } from '../api.js'

const box = ref(null)
const field = ref(null)
const text = ref('')
watch(() => prompt.open, (open) => { if (open) { text.value = ''; nextTick(() => (field.value || box.value)?.focus()) } })
function key(e) {
  if (prompt.notice) { if (['Enter', 'Escape', 'y', 'n'].includes(e.key)) answer('ok'); return }
  if (e.target.tagName === 'INPUT') { if (e.key === 'Enter') answer(text.value || null); if (e.key === 'Escape') answer(null); return }
  if (e.key === 'y' || e.key === 'Enter') answer(state.dryRun ? 'dry' : 'yes')
  else if (e.key === 'd' && prompt.allowDry) answer('dry')
  else if (e.key === 'n' || e.key === 'Escape') answer(null)
}
</script>

<template>
  <div v-if="prompt.open" class="modal" @click.self="answer(null)">
    <div ref="box" class="dialog" tabindex="0" role="dialog" aria-modal="true" @keydown="key">
      <div class="q">{{ prompt.question }}</div>
      <pre v-if="prompt.detail" class="text dim" style="margin-top: 8px">{{ prompt.detail }}</pre>
      <div v-for="l in prompt.links" :key="l.href" style="margin-top: 6px">
        <a class="accent" :href="l.href" target="_blank" rel="noopener">{{ l.label }}</a>
      </div>
      <div v-if="prompt.input" style="margin-top: 10px">
        <input ref="field" v-model="text" :type="prompt.input.password ? 'password' : 'text'" :placeholder="prompt.input.placeholder"
               style="width: 100%" autofocus>
      </div>
      <div class="row" style="margin-top: 12px">
        <template v-if="prompt.notice">
          <button class="primary" @click="answer('ok')">ok</button>
        </template>
        <template v-else-if="prompt.input">
          <button class="primary" @click="answer(text || null)">ok</button>
          <button @click="answer(null)">cancel</button>
        </template>
        <template v-else>
          <button class="primary" @click="answer(state.dryRun ? 'dry' : 'yes')">
            <span class="k">y</span> {{ state.dryRun ? 'yes (dry run is on)' : 'yes' }}</button>
          <button v-if="prompt.allowDry" @click="answer('dry')"><span class="k">d</span> dry run</button>
          <button @click="answer(null)"><span class="k">n</span> no</button>
        </template>
      </div>
    </div>
  </div>
</template>
