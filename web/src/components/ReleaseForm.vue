<script setup>
// New release: tag, title, notes, draft / pre-release (GitHub only), target branch, generated notes.
// New tag (mode 'tag'): the tag and its target only, for repos whose workflow makes the release on a tag push.
import { reactive, watch } from 'vue'
import { log } from '../api.js'
import { act } from '../load.js'

const props = defineProps({
  repo: { type: String, default: '' },
  suggest: { type: String, default: '' }, // the next tag, e.g. v1.4.1
  repos: { type: Array, default: () => [] }, // repo names for the picker (empty: the repo is fixed)
  mode: { type: String, default: 'release' }, // 'release' (the API) or 'tag' (push the tag only)
  publishes: { type: String, default: '' }, // the workflow that makes releases on tag pushes, if known
})
const emit = defineEmits(['done', 'cancel'])
const f = reactive({ repo: props.repo, tag: props.suggest, title: '', notes: '', draft: false, prerelease: false, target: '', generate: true })
watch(() => props.suggest, (t) => { if (!f.tag) f.tag = t })
watch(() => props.repo, (r) => { if (r) f.repo = r })
const gitlab = () => f.repo.startsWith('gitlab:')

async function submit() {
  if (!f.repo || !f.tag.trim()) { log('a repo and a tag, please', 'warn'); return }
  if (props.mode === 'tag') {
    const then = props.publishes ? `\n${props.publishes} creates the release from it.` : ''
    const text = await act(`Push the tag ${f.tag} on ${f.target || 'the default branch'} of ${f.repo}?`, '/tags',
      { repo: f.repo, tag: f.tag.trim(), target: f.target }, { detail: `only the tag is pushed${then}` })
    if (text !== null) emit('done', { text, tag: f.tag.trim(), ok: !text.startsWith('error') && !text.includes('[dry-run]') })
    return
  }
  const kind = f.draft ? 'draft' : f.prerelease ? 'pre-release' : 'release'
  const text = await act(`Create the ${kind} ${f.tag} of ${f.repo}?`, '/releases', {
    repo: f.repo, tag: f.tag.trim(), title: f.title, notes: f.notes, target: f.target,
    draft: !gitlab() && f.draft, prerelease: !gitlab() && f.prerelease, generate_notes: !gitlab() && !f.notes.trim() && f.generate,
  }, { detail: `${f.title || f.tag}${f.notes ? '\n\n' + f.notes : ''}` })
  if (text === null) return
  emit('done', { text, tag: f.tag.trim(), ok: !text.startsWith('error') && !text.includes('[dry-run]') })
}
</script>

<template>
  <div class="box" @keydown.esc.stop="emit('cancel')">
    <h2>{{ mode === 'tag' ? 'new tag' : 'new release' }}</h2>
    <p v-if="mode === 'tag'" class="dim">Only the tag is pushed.{{ publishes ? ` ${publishes} creates the release from it.` : ' A workflow that publishes on tag pushes makes the release.' }}</p>
    <p v-else-if="publishes" class="warn">{{ publishes }} creates releases when a tag is pushed: a new tag (T) fits this repo better, a release made here first would collide with it.</p>
    <div class="form">
      <label>repo</label>
      <input v-if="repos.length" v-model="f.repo" list="release-repos" placeholder="owner/name or gitlab:group/project">
      <span v-else class="accent">{{ f.repo }}</span>
      <label>tag</label><input v-model="f.tag" placeholder="v1.4.0" autofocus @keydown.enter="submit">
      <template v-if="mode !== 'tag'"><label>title</label><input v-model="f.title" :placeholder="f.tag || 'the tag'" @keydown.enter="submit"></template>
      <label>target</label><input v-model="f.target" :placeholder="mode === 'tag' ? 'default branch' : 'default branch (only for a new tag)'">
      <template v-if="!gitlab() && mode !== 'tag'">
        <label>kind</label>
        <span class="row">
          <label class="check"><input v-model="f.draft" type="checkbox"> draft</label>
          <label class="check"><input v-model="f.prerelease" type="checkbox"> pre-release</label>
          <label class="check" :class="{ dim: f.notes.trim() }"><input v-model="f.generate" type="checkbox" :disabled="!!f.notes.trim()"> notes from merged PRs</label>
        </span>
      </template>
    </div>
    <datalist id="release-repos"><option v-for="r in repos" :key="r" :value="r" /></datalist>
    <textarea v-if="mode !== 'tag'" v-model="f.notes" rows="10" placeholder="release notes (Markdown)" style="margin-top: 8px" />
    <div class="row" style="margin-top: 8px">
      <button class="primary" @click="submit">{{ mode === 'tag' ? 'push tag' : 'create release' }}</button>
      <button @click="emit('cancel')">cancel <span class="dim">(esc)</span></button>
    </div>
  </div>
</template>
