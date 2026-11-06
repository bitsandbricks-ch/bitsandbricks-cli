<script setup>
// Settings: logins, the defaults repo and which repos to manage, API keys, sessions and plugin secrets.
// Everything a CLI user keeps in files lives here, per user: nobody else sees or changes yours.
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { del, get, loadMe, log, post, put, state } from '../api.js'
import { deviceSignIn } from '../signin.js'
import { ago, ask, tellRevoked } from '../ui.js'

const router = useRouter()
const s = ref(null)
const keys = ref([])
const sessions = ref([])
const secrets = ref([])
const newKey = ref({ name: 'laptop', scope: 'read', days: 90 })
const fresh = ref(null) // the key just made: shown once
const origin = window.location.origin
const LISTS = ['orgs', 'repos', 'exclude', 'gitlab_groups', 'gitlab_repos']
const linking = ref(null) // { user_code, verification_uri } while connecting a login with GitHub's device flow
let stopLink = null

function connectDevice(p) {
  stopLink?.()
  stopLink = deviceSignIn(p, { link: true }, {
    code: (d) => { linking.value = d },
    done: async () => { linking.value = null; await loadMe(); log(`connected ${p}`) },
    fail: (msg) => { linking.value = null; log(msg, 'error') },
  })
}
function cancelLink() { stopLink?.(); stopLink = null; linking.value = null }
onBeforeUnmount(() => stopLink?.())

const asText = (v) => (v || []).join(', ')
async function load() {
  try {
    const got = await get('/settings')
    s.value = { ...got, ...Object.fromEntries(LISTS.map((k) => [k, asText(got[k])])) }
    ;[keys.value, sessions.value, secrets.value] = await Promise.all([get('/keys'), get('/sessions'), get('/secrets')])
  } catch (e) { log(e.message, 'error') }
}
async function saveSettings() {
  try {
    const { theme, plugins_off, ...rest } = s.value // eslint-disable-line no-unused-vars
    await put('/settings', rest)
    log('settings saved', 'ok')
    await loadMe()
    load()
  } catch (e) { log(e.message, 'error') }
}
async function unlink(i) {
  if (!(await ask(`Disconnect ${i.provider} (${i.login})? Mason deletes its token and revokes it where ${i.provider} lets it.`, { allowDry: false }))) return
  try { const got = await del(`/me/identities/${i.id}`); await loadMe(); await tellRevoked(got) } catch (e) { log(e.message, 'error') }
}
async function makeKey() {
  try {
    const body = { name: newKey.value.name, scope: newKey.value.scope }
    if (newKey.value.days) body.days = Number(newKey.value.days)
    fresh.value = await post('/keys', body)
    load()
  } catch (e) { log(e.message, 'error') }
}
async function copy(text) {
  try { await navigator.clipboard.writeText(text); log('copied', 'ok') } catch { log('select the key and copy it', 'warn') }
}
async function revoke(k) {
  if (!(await ask(`Revoke the key ${k.name} (${k.prefix})?`, { allowDry: false }))) return
  try { await del(`/keys/${k.id}`); load() } catch (e) { log(e.message, 'error') }
}
async function endSession(x) {
  try { await del(`/sessions/${x.id}`); load() } catch (e) { log(e.message, 'error') }
}
async function setSecret(x) {
  const v = await ask(`Value for ${x.label}`, { input: { password: true, placeholder: 'kept encrypted; never shown again' }, allowDry: false })
  if (!v) return
  try { await put(`/secrets/${x.name}`, { value: v }); load() } catch (e) { log(e.message, 'error') }
}
async function dropSecret(x) {
  try { await del(`/secrets/${x.name}`); load() } catch (e) { log(e.message, 'error') }
}
async function deleteAccount() {
  const name = await ask('Delete your account? Type your user name to confirm. Your keys, sessions and secrets go too; your repos stay as they are.', { input: { placeholder: state.me.user.name }, allowDry: false })
  if (name !== state.me.user.name) return
  try {
    const got = await del('/me')
    state.me = { user: null, providers: state.me.providers }
    await tellRevoked(got)
    router.push('/login')
  } catch (e) { log(e.message, 'error') }
}
onMounted(load)
</script>

<template>
  <div class="view">
    <div v-if="state.me?.user" class="scroll pad" style="max-width: 110ch">
      <div class="box">
        <div class="accent">accounts</div>
        <div v-for="i in state.me.identities" :key="i.id" class="row">
          <span class="ok">●</span><span>{{ i.provider }}</span><span class="accent">{{ i.login }}</span>
          <span class="dim">{{ i.host }} · scopes {{ i.scopes || '-' }}</span><span class="spacer" />
          <button @click="unlink(i)">disconnect</button>
        </div>
        <div class="row" style="margin-top: 6px">
          <template v-for="p in state.me.providers" :key="p">
            <template v-if="!state.me.identities.some((i) => i.provider === p)">
              <button v-if="state.me.sign_in?.[p] === 'device'" :disabled="!!linking" @click="connectDevice(p)">connect {{ p }}</button>
              <a v-else class="btn" :href="`/api/v1/auth/${p}/login?link=1`">connect {{ p }}</a>
            </template>
          </template>
        </div>
        <p v-if="linking">Enter <span class="accent">{{ linking.user_code }}</span> at
          <a class="accent" :href="linking.verification_uri" target="_blank" rel="noopener">{{ linking.verification_uri }}</a>
          <span class="dim"> (waiting<span class="cursor" />)</span>
          <button @click="cancelLink">cancel</button></p>
      </div>

      <div v-if="s" class="box">
        <div class="accent">defaults and repos</div>
        <p class="dim">The defaults repo holds mason.toml, like <span class="accent">mason init</span> sets up. Lists take names separated by commas.</p>
        <div class="form">
          <label>defaults repo</label><input v-model="s.defaults" placeholder="acme/defaults or gitlab:grp/defaults">
          <label>branch or tag</label><input v-model="s.ref" placeholder="(default branch)">
          <label>GitHub orgs</label><input v-model="s.orgs" placeholder="acme, acme-labs">
          <label>extra repos</label><input v-model="s.repos" placeholder="someone/tool">
          <label>leave out</label><input v-model="s.exclude" placeholder="acme/archive">
          <label>GitLab groups</label><input v-model="s.gitlab_groups" placeholder="grp, grp/sub">
          <label>GitLab projects</label><input v-model="s.gitlab_repos" placeholder="gitlab:grp/proj">
        </div>
        <button class="primary" style="margin-top: 8px" @click="saveSettings">save</button>
      </div>

      <div class="box">
        <div class="accent">API keys</div>
        <p class="dim">For the TUI and scripts: <span class="accent">mason login {{ origin }}</span> makes one for you, or make one here and pass <span class="accent">--key</span>.</p>
        <div v-if="fresh" class="box">
          <div class="warn">copy it now: it isn't shown again</div>
          <div class="row"><span class="secret">{{ fresh.key }}</span><button @click="copy(fresh.key)">copy</button><button @click="fresh = null">done</button></div>
        </div>
        <div class="row">
          <input v-model="newKey.name" placeholder="name" aria-label="key name">
          <select v-model="newKey.scope" aria-label="scope"><option value="read">read</option><option value="write">write</option></select>
          <select v-model="newKey.days" aria-label="expires"><option :value="30">30 days</option><option :value="90">90 days</option><option :value="365">a year</option><option :value="null">never expires</option></select>
          <button class="primary" @click="makeKey">new key</button>
        </div>
        <table class="table" style="margin-top: 8px">
          <tr v-for="k in keys.filter((x) => !x.revoked)" :key="k.id">
            <td>{{ k.name }}</td><td class="dim">{{ k.prefix }}…</td><td>{{ k.scope }}</td>
            <td class="dim">made {{ ago(k.created_at) }} · used {{ ago(k.last_used_at) }}{{ k.last_ip ? ` from ${k.last_ip}` : '' }} · {{ k.expires_at ? `expires ${k.expires_at.slice(0, 10)}` : 'no expiry' }}</td>
            <td class="right"><button class="danger" @click="revoke(k)">revoke</button></td>
          </tr>
        </table>
      </div>

      <div class="box">
        <div class="accent">sessions</div>
        <table class="table">
          <tr v-for="x in sessions" :key="x.id">
            <td :class="x.current ? 'ok' : ''">{{ x.current ? 'this browser' : x.id }}</td><td class="dim">{{ x.ip }}</td>
            <td class="dim" :title="x.user_agent">{{ (x.user_agent || '').slice(0, 60) }}</td><td class="dim">seen {{ ago(x.seen_at) }}</td>
            <td class="right"><button v-if="!x.current" @click="endSession(x)">sign out</button></td>
          </tr>
        </table>
      </div>

      <div v-if="secrets.length" class="box">
        <div class="accent">plugin secrets</div>
        <p class="dim">Tokens plugins need (kept encrypted, used only for your requests).</p>
        <div v-for="x in secrets" :key="x.name" class="row">
          <span :class="x.set ? 'ok' : 'dim'">{{ x.set ? '●' : '○' }}</span><span>{{ x.label }}</span>
          <span class="dim">{{ x.set ? `set ${ago(x.updated_at)}` : 'not set' }}</span><span class="spacer" />
          <button @click="setSecret(x)">{{ x.set ? 'replace' : 'set' }}</button>
          <button v-if="x.set" @click="dropSecret(x)">remove</button>
        </div>
      </div>

      <div class="box">
        <div class="bad">delete my account</div>
        <p class="dim">Revokes Mason's tokens at GitHub and GitLab and removes everything it keeps about you.</p>
        <button class="danger" @click="deleteAccount">delete account</button>
      </div>
    </div>
  </div>
</template>
