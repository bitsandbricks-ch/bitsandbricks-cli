<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { get, loadMe, log, post, state } from './api.js'
import { TABS } from './router.js'
import { typing } from './keys.js'
import { prompt } from './ui.js'
import PromptDialog from './components/PromptDialog.vue'

const route = useRoute()
const router = useRouter()
const themes = ['phosphor', 'amber', 'paper']
const theme = ref(localStorage.getItem('bnb-theme') || 'phosphor')
const plugins = ref([])
watch(theme, (t) => { document.documentElement.dataset.theme = t; try { localStorage.setItem('bnb-theme', t) } catch { /* private mode */ } }, { immediate: true })

const signedIn = computed(() => !!state.me?.user)
const who = computed(() => (state.me?.identities || []).map((i) => `${i.provider}:${i.login}`).join(' '))
const last = computed(() => state.log[0])
const rate = computed(() => {
  const c = state.rate?.github?.core
  return c ? `gh ${c.remaining}/${c.limit}` : ''
})

async function refreshRate() {
  if (!signedIn.value) return
  try { state.rate = await get('/rate') } catch { /* not important */ }
}

onMounted(async () => {
  await loadMe()
  guard()
  if (signedIn.value) {
    refreshRate()
    try { plugins.value = (await get('/plugins')).plugins.filter((p) => p.on && p.tabs.length) } catch { /* none */ }
  }
})
function guard() {
  if (!state.loaded) return
  if (!signedIn.value && !route.meta.public) router.replace({ name: 'login', query: route.path !== '/' ? { next: route.fullPath } : {} })
}
watch(() => route.fullPath, guard)
watch(signedIn, guard)

async function logout() {
  try { await post('/auth/logout') } catch (e) { log(e.message, 'error') }
  state.me = { user: null, providers: state.me?.providers || [] }
  router.replace('/login')
}

window.addEventListener('keydown', (e) => {
  if (prompt.open || typing(e) || e.ctrlKey || e.metaKey || e.altKey || !signedIn.value) return
  const tab = TABS.find((t) => t.key === e.key)
  if (tab) { router.push(tab.path); e.preventDefault(); return }
  if (e.key === 't') { theme.value = themes[(themes.indexOf(theme.value) + 1) % themes.length] }
  else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
    const i = TABS.findIndex((t) => t.name === route.name)
    if (i >= 0) { router.push(TABS[(i + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length].path); e.preventDefault() }
  } else if (e.key === '?') { router.push('/console'); log('keys: 1-9 tabs · ←→ next tab · ↑↓/jk move · Enter act · / filter · r reload · t theme · : console') }
})
</script>

<template>
  <div v-if="!state.loaded" class="center"><span class="cursor">connecting</span></div>
  <router-view v-else-if="!signedIn || route.meta.public" />
  <div v-else class="screen">
    <header class="top">
      <span class="prompt accent">{{ state.me.user.name.toLowerCase().replace(/\s+/g, '') }}@mortar</span><span class="dim hide-xs">:~$</span>
      <span class="dim hide-sm">{{ who }}</span>
      <span class="spacer" />
      <span v-if="state.busy" class="warn">working…</span>
      <span class="dim hide-sm" :title="'API calls left at GitHub'">{{ rate }}</span>
      <label class="check" title="every change only shows what it would do">
        <input v-model="state.dryRun" type="checkbox"> <span :class="state.dryRun ? 'warn' : 'dim'">dry<span class="hide-xs"> run</span></span>
      </label>
      <button title="t switches the theme" aria-label="switch the theme" @click="theme = themes[(themes.indexOf(theme) + 1) % themes.length]"><span class="hide-xs">{{ theme }}</span><span class="show-xs">◐</span></button>
      <button @click="logout"><span class="hide-xs">logout</span><span class="show-xs">exit</span></button>
    </header>
    <nav class="tabs" aria-label="views">
      <router-link v-for="t in TABS" :key="t.name" :to="t.path" :class="{ active: route.name === t.name }">
        <span class="k">{{ t.key }}</span>{{ t.label }}
      </router-link>
      <router-link v-for="p in plugins" :key="p.name" :to="`/plugins/${p.name}/${p.tabs[0].id}`"
                   :class="{ active: route.params.plugin === p.name }">
        <span class="k">+</span>{{ p.tabs[0].title.toLowerCase() }}
      </router-link>
    </nav>
    <main><router-view @rate="refreshRate" /></main>
    <footer class="foot">
      <span v-if="last" class="last" :class="last.kind === 'error' ? 'bad' : last.kind === 'ok' ? 'ok' : 'dim'"
            :title="last.text">[{{ last.at }}] {{ last.text }}</span>
      <span v-else class="last dim">ready</span>
      <span class="keys"><b>1-9</b> tabs <b>↑↓</b> move <b>⏎</b> act <b>/</b> filter <b>r</b> reload <b>t</b> theme <b>:</b> console <b>?</b> help</span>
    </footer>
  </div>
  <PromptDialog />
</template>
