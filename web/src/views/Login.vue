<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRoute } from 'vue-router'
import { state } from '../api.js'
import { deviceSignIn } from '../signin.js'

const route = useRoute()
const next = computed(() => encodeURIComponent(String(route.query.next || '/')))
const providers = computed(() => state.me?.providers || [])
const method = (p) => state.me?.sign_in?.[p] || 'redirect'
const device = ref(null)     // { user_code, verification_uri, expires_in } while GitHub waits for the code
const error = ref('')
const copied = ref(false)
let stop = null

async function startDevice(p) {
  error.value = ''
  copied.value = false
  stop?.()
  stop = deviceSignIn(p, { next: String(route.query.next || '/') }, {
    code: (d) => { device.value = d },
    done: (to) => { window.location.assign(to) },
    fail: (msg) => { device.value = null; error.value = msg },
  })
}

function cancel() { stop?.(); stop = null; device.value = null }

async function copy() {
  try { await navigator.clipboard.writeText(device.value.user_code); copied.value = true } catch { copied.value = false }
}

onBeforeUnmount(() => stop?.())
const origin = window.location.origin
const BANNER = [
  '███    ███  ██████  ██████  ████████  █████  ██████',
  '████  ████ ██    ██ ██   ██    ██    ██   ██ ██   ██',
  '██ ████ ██ ██    ██ ██████     ██    ███████ ██████',
  '██  ██  ██ ██    ██ ██   ██    ██    ██   ██ ██   ██',
  '██      ██  ██████  ██   ██    ██    ██   ██ ██   ██',
].join('\n')
</script>

<template>
  <div class="center">
    <div style="max-width: 720px; width: 100%">
      <pre class="banner" aria-hidden="true">{{ BANNER }}</pre>
      <p><span class="accent">$</span> mason login<span class="cursor" /></p>
      <p class="dim">Your GitHub and GitLab repos, pull requests, runs, approvals, releases, security alerts and
        defaults, from one terminal in the browser: Mason holds your bricks together. You sign in with your own account; Mason only ever acts
        with your token, so you see and change no more than you could yourself.</p>
      <p v-if="error || route.query.error" class="bad">error: {{ error || route.query.error }}</p>
      <div v-if="device" class="box" style="margin: 18px 0">
        <p>1. Open <a class="accent" :href="device.verification_uri" target="_blank" rel="noopener">{{ device.verification_uri }}</a></p>
        <p>2. Enter this code:</p>
        <p class="row"><span class="banner" style="font-size: 1.6em; letter-spacing: 0.15em" data-test="user-code">{{ device.user_code }}</span>
          <button @click="copy">{{ copied ? 'copied' : 'copy' }}</button></p>
        <p class="dim">Waiting for GitHub<span class="cursor" /> This page signs you in by itself once you have entered the code.
          The code is good for {{ Math.round(device.expires_in / 60) }} minutes.</p>
        <button @click="cancel">cancel</button>
      </div>
      <div v-else class="row" style="margin: 18px 0">
        <template v-for="p in providers" :key="p">
          <button v-if="method(p) === 'device'" class="btn primary" @click="startDevice(p)">
            [ sign in with {{ p === 'github' ? 'GitHub' : 'GitLab' }} ]</button>
          <a v-else class="btn primary" :href="`/api/v1/auth/${p}/login?next=${next}`">
            [ sign in with {{ p === 'github' ? 'GitHub' : 'GitLab' }} ]</a>
        </template>
        <span v-if="!providers.length" class="bad">No sign-in is set up: the admin sets GITHUB_CLIENT_ID or GITLAB_CLIENT_ID.</span>
      </div>
      <p class="dim">The CLI: <span class="accent">mason login {{ origin }}</span> then <span class="accent">mason tui</span>.</p>
    </div>
  </div>
</template>
