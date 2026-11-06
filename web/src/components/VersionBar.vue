<script setup>
// The version file against the latest release, with bump (b: a PR with the next version and its changelog
// section) and release (R: push v<version> once the bump is merged). Shown on a repo's and its releases' pages.
import { computed } from 'vue'
import { act } from '../load.js'
import { ask } from '../ui.js'
import { useKeys } from '../keys.js'

const props = defineProps({ repo: { type: String, required: true }, version: { type: Object, default: null } })
const emit = defineEmits(['done'])
const v = computed(() => props.version)

async function bump() {
  if (!v.value) return
  const level = await ask(`Bump ${props.repo} (${v.value.file} says ${v.value.version || '?'})`,
    { input: { placeholder: 'patch, minor, major or 1.4.0 (empty: patch)' }, allowDry: false })
  if (level === null) return
  const lv = (level || 'patch').trim()
  const text = await act(`Open a PR in ${props.repo} that sets the version (${lv}) and adds the changelog section?`, '/bump',
    { repo: props.repo, level: lv }, { detail: 'After its merge, R pushes the tag and the repo\'s workflow releases it.' })
  if (text !== null) emit('done', { text, ok: !text.startsWith('error') && !text.includes('[dry-run]') })
}
async function release() {
  if (!v.value?.todo) return
  const text = await act(`Push the tag ${v.value.tag} on ${v.value.base} of ${props.repo}?`, '/release/version', { repo: props.repo },
    { detail: `${v.value.file} says ${v.value.version}, latest release ${v.value.released || 'none'}. The repo's release workflow takes it from there.` })
  if (text !== null) emit('done', { text, ok: !text.startsWith('error') && !text.includes('[dry-run]') })
}
useKeys({ b: bump, R: release })
</script>

<template>
  <div v-if="v" class="box">
    <div class="row">
      <span class="dim">{{ v.file }}</span> <span class="accent">{{ v.version || '?' }}</span>
      <span class="dim">· released</span> <span>{{ v.released || 'none' }}</span>
      <span v-if="v.todo" class="warn">· {{ v.tag }} is ready to release</span>
      <span v-else class="dim">· released; b bumps it</span>
      <span class="spacer" />
      <button @click="bump"><span class="k">b</span> bump</button>
      <button :disabled="!v.todo" :class="{ primary: v.todo }" @click="release"><span class="k">R</span> release {{ v.todo ? v.tag : '' }}</button>
    </div>
  </div>
</template>
