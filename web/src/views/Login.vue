<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { state } from '../api.js'

const route = useRoute()
const next = computed(() => encodeURIComponent(String(route.query.next || '/')))
const providers = computed(() => state.me?.providers || [])
const origin = window.location.origin
const BANNER = [
  '┌┐ ┌┐┌┌┐   ┌─┐┌─┐┌─┐┬─┐┌─┐┌┬┐┌─┐┬─┐',
  '├┴┐│││├┴┐  │ │├─┘├┤ ├┬┘├─┤ │ │ │├┬┘',
  '└─┘┘└┘└─┘  └─┘┴  └─┘┴└─┴ ┴ ┴ └─┘┴└─',
].join('\n')
</script>

<template>
  <div class="center">
    <div style="max-width: 720px; width: 100%">
      <pre class="banner" aria-hidden="true">{{ BANNER }}</pre>
      <p><span class="accent">$</span> bnb login<span class="cursor" /></p>
      <p class="dim">Your GitHub and GitLab repos, pull requests, runs, approvals, releases, security alerts and
        defaults, from one terminal in the browser. You sign in with your own account; the operator only ever acts
        with your token, so you see and change no more than you could yourself.</p>
      <p v-if="route.query.error" class="bad">error: {{ route.query.error }}</p>
      <div class="row" style="margin: 18px 0">
        <a v-if="providers.includes('github')" class="btn primary" :href="`/api/v1/auth/github/login?next=${next}`">
          [ sign in with GitHub ]</a>
        <a v-if="providers.includes('gitlab')" class="btn primary" :href="`/api/v1/auth/gitlab/login?next=${next}`">
          [ sign in with GitLab ]</a>
        <span v-if="!providers.length" class="bad">No sign-in is set up: the admin sets GITHUB_CLIENT_ID or GITLAB_CLIENT_ID.</span>
      </div>
      <p class="dim">The CLI: <span class="accent">bnb login {{ origin }}</span> then <span class="accent">bnb tui</span>.</p>
    </div>
  </div>
</template>
