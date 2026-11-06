<script setup>
// The defaults editor: the defaults repo's files, a text editor with validation, templates to start
// from, and rulesets imported from a live repo. Saving opens a PR (or commits, for those who may).
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ApiError, get, log, post, put } from '../api.js'
import { useKeys } from '../keys.js'
import { ask } from '../ui.js'
import TextPane from '../components/TextPane.vue'

const info = ref(null)
const TOML = computed(() => info.value?.path || 'mason.toml')
const missing = ref('')
const path = ref('')
const text = ref('')
const original = ref('')
const problems = ref([])
const mode = ref('pr')
const result = ref('')
const templates = ref([])
const showTemplates = ref(false)
const editor = ref(null)
const dirty = computed(() => text.value !== original.value)

async function load() {
  missing.value = ''
  try {
    info.value = await get('/defaults')
    if (!path.value) open(TOML.value, info.value.toml || '', info.value.problems)
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) missing.value = e.message
    else log(e.message, 'error')
  }
}
function open(p, t, probs = []) {
  path.value = p
  text.value = t
  original.value = info.value?.files?.includes(p) ? t : ''
  problems.value = probs
  result.value = ''
}
async function pick(p) {
  if (dirty.value && !(await ask(`Drop your changes to ${path.value}?`, { allowDry: false }))) return
  try {
    const f = await get(`/defaults/file?path=${encodeURIComponent(p)}`)
    open(f.path, f.text, f.problems)
  } catch (e) { log(e.message, 'error') }
}
async function check() {
  try { problems.value = (await post('/defaults/validate', { path: path.value, text: text.value })).problems } catch (e) { log(e.message, 'error') }
  return problems.value
}
async function save(remove = false) {
  if (!path.value) return
  if (!remove && (await check()).length && !(await ask(`${path.value} has problems. Save it anyway?`, { allowDry: false, detail: problems.value.join('\n') }))) return
  const how = mode.value === 'pr' ? 'in a pull request' : 'straight to the branch'
  if (!(await ask(`${remove ? 'Remove' : 'Save'} ${path.value} ${how}?`, { allowDry: false }))) return
  try {
    const got = await put('/defaults/file', { path: path.value, text: text.value, mode: mode.value, delete: remove, force: true })
    result.value = `${remove ? 'removed' : 'saved'} ${path.value}: ${got.url}`
    log(result.value, 'ok')
    original.value = text.value
    await load()
  } catch (e) { result.value = `error: ${e.message}`; log(e.message, 'error') }
}
async function newFile() {
  const p = await ask('Path of the new file', { input: { placeholder: 'files/default/.github/CODEOWNERS' }, allowDry: false })
  if (p) open(p.trim(), '')
}
async function loadTemplates() {
  showTemplates.value = !showTemplates.value
  if (showTemplates.value && !templates.value.length) {
    try { templates.value = await get('/defaults/templates') } catch (e) { log(e.message, 'error') }
  }
}
function useTemplate(t) {
  if (t.kind === 'group') {
    if (path.value !== TOML.value) { log(`open ${TOML.value} first, then add the group`, 'warn'); return }
    text.value = `${text.value.replace(/\s*$/, '')}\n${t.text}`
  } else {
    open(t.path, t.text.replace(/^\n/, ''))
  }
  showTemplates.value = false
  check()
}
async function importRulesets() {
  const repo = await ask('Copy the live rulesets of which repo?', { input: { placeholder: 'acme/web' }, allowDry: false })
  if (!repo) return
  try {
    const got = await post('/defaults/ruleset-from', { repo: repo.trim() })
    if (!got.length) { log(`${repo} has no rulesets`, 'warn'); return }
    open(got[0].path, got[0].text)
    result.value = got.map((r) => `${r.name} -> ${r.path}`).join('\n') + (got.length > 1 ? '\nopened the first; the others are listed above' : '')
    templates.value = [...got.map((r) => ({ id: r.path, kind: 'ruleset', title: `Imported: ${r.name}`, help: `from ${repo}`, path: r.path, text: r.text })), ...templates.value]
  } catch (e) { log(e.message, 'error') }
}
function tab(e) { // a tab key inserts two spaces instead of leaving the editor
  if (e.key !== 'Tab') return
  e.preventDefault()
  const el = e.target
  const [s, end] = [el.selectionStart, el.selectionEnd]
  text.value = text.value.slice(0, s) + '  ' + text.value.slice(end)
  requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = s + 2 })
}
function saveKey(e) { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); save() } }
useKeys({ n: newFile, o: loadTemplates, i: importRulesets, v: check, e: () => editor.value?.focus() })
onMounted(load)
</script>

<template>
  <div class="view">
    <div v-if="missing" class="pad">
      <div class="box"><span class="warn">{{ missing }}</span><br><br>The defaults repo holds <span class="accent">mason.toml</span>
        (groups, settings, rulesets, files) the same way the CLI reads it. Pick one in <RouterLink to="/settings">settings</RouterLink>.</div>
    </div>
    <template v-else-if="info">
      <div class="toolbar">
        <span class="dim">{{ info.provider }}:</span><span class="accent">{{ info.repo }}</span><span v-if="info.ref" class="dim">@{{ info.ref }}</span>
        <span class="spacer" />
        <button @click="newFile"><span class="k">n</span> new file</button>
        <button @click="loadTemplates"><span class="k">o</span> templates</button>
        <button v-if="info.provider === 'github'" @click="importRulesets"><span class="k">i</span> import rulesets</button>
      </div>
      <div class="split">
        <div class="scroll" style="display: flex; flex-direction: column">
          <div class="toolbar">
            <span class="accent">{{ path || '(no file)' }}</span><span v-if="dirty" class="warn">[modified]</span>
            <span class="spacer" />
            <button @click="check"><span class="k">v</span> validate</button>
            <select v-model="mode" aria-label="how to save"><option value="pr">as a pull request</option><option value="commit">commit directly</option></select>
            <button class="primary" :disabled="!path" @click="save()">save <span class="k">^s</span></button>
            <button v-if="info.files.includes(path)" class="danger" @click="save(true)">remove</button>
          </div>
          <textarea ref="editor" v-model="text" class="editor grow" spellcheck="false" aria-label="file text"
                    @keydown="tab($event); saveKey($event)" @blur="path && check()" />
        </div>
        <div class="scroll pad">
          <div v-if="showTemplates" class="box">
            <div class="accent">templates</div>
            <div v-for="t in templates" :key="t.id" class="row" style="margin-top: 6px">
              <button @click="useTemplate(t)">use</button>
              <span>{{ t.title }}</span><span class="dim">{{ t.help }}</span>
            </div>
          </div>
          <div class="box">
            <div class="accent">problems</div>
            <div v-if="!problems.length" class="ok">none found</div>
            <div v-for="(p, i) in problems" :key="i" class="bad">- {{ p }}</div>
          </div>
          <TextPane v-if="result" :text="result" />
          <div class="accent" style="margin-top: 8px">files</div>
          <div v-for="f in info.files" :key="f">
            <a href="#" :class="{ accent: f === path }" @click.prevent="pick(f)">{{ f }}</a>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
