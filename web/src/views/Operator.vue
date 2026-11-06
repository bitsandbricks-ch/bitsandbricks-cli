<script setup>
// The operator page: the whole server (accounts, numbers, problems, plugins for everyone). Only for users
// made operators with `mason operator add` where Mason runs; it never shows another user's settings.
import { onMounted, ref } from 'vue'
import { get, log, post, put } from '../api.js'
import { ago, ask } from '../ui.js'

const d = ref(null)
const error = ref('')
async function load() {
  error.value = ''
  try { d.value = await get('/operator') } catch (e) { error.value = e.message }
}
async function plugin(p, on) {
  try { d.value = await put(`/operator/plugins/${p.name}`, { on }); log(`${p.name} ${on ? 'on' : 'off'} for everyone`, 'ok') } catch (e) { log(e.message, 'error'); load() }
}
async function signOut(u) {
  if (!(await ask(`Sign ${u.name} out everywhere?`, { allowDry: false, detail: 'Ends all their sessions and revokes their API keys. Their account and settings stay.' }))) return
  try { const got = await post(`/operator/users/${u.id}/sign-out`); log(`${u.name}: ${got.sessions} sessions ended, ${got.keys} keys revoked`, 'ok'); load() } catch (e) { log(e.message, 'error') }
}
onMounted(load)
</script>

<template>
  <div class="view">
    <div v-if="error" class="pad bad">{{ error }}</div>
    <div v-else-if="d" class="scroll pad" style="max-width: 120ch">
      <div class="box">
        <div class="accent">server</div>
        <div class="dim">{{ d.server.url }} · sign-in {{ d.server.sign_in }}<template v-if="d.server.allow_users.length"> · users {{ d.server.allow_users.join(', ') }}</template><template v-if="d.server.allow_orgs.length"> · orgs {{ d.server.allow_orgs.join(', ') }}</template> · at most {{ d.server.max_users || 'any number of' }} accounts</div>
        <div class="dim">{{ d.stats.users }} users · {{ d.stats.sessions }} sessions · {{ d.stats.keys }} working keys · {{ d.stats.transports.length }} cached connections</div>
        <div v-for="p in d.problems" :key="p" class="bad">problem: {{ p }}</div>
        <div v-for="b in d.broken" :key="b" class="warn">plugin failed to load: {{ b }}</div>
        <div class="dim">operators are added and removed only where Mason runs: <span class="accent">mason operator add github:LOGIN</span></div>
      </div>

      <div class="box">
        <div class="accent">plugins for everyone</div>
        <div v-if="!d.plugins.length" class="dim">no plugins installed (pip install next to Mason, then restart)</div>
        <div v-for="p in d.plugins" :key="p.name" class="row">
          <span :class="p.off_for_all ? 'dim' : ''">{{ p.name }}</span><span class="dim grow">{{ p.description }}</span>
          <label class="check"><input type="checkbox" :checked="!p.off_for_all" @change="plugin(p, $event.target.checked)"> on for everyone</label>
        </div>
        <div v-if="d.server.plugins_never_loaded" class="dim">never loaded (MASON_PLUGINS_OFF): {{ d.server.plugins_never_loaded }}</div>
      </div>

      <div class="box">
        <div class="accent">accounts</div>
        <table class="table" style="margin-top: 6px">
          <tr v-for="u in d.users" :key="u.id">
            <td>#{{ u.id }} {{ u.name }}<span v-if="u.operator" class="warn"> operator</span></td>
            <td class="dim">{{ u.logins.join(', ') }}</td>
            <td class="dim">{{ u.keys }} keys · {{ u.sessions }} sessions</td>
            <td class="dim">{{ u.last_login ? ago(u.last_login) : 'never' }}</td>
            <td><button v-if="u.sessions || u.keys" @click="signOut(u)">sign out</button></td>
          </tr>
        </table>
      </div>
    </div>
  </div>
</template>
