<script setup>
// Container images (GHCR and the GitLab registry): newest tags, the release's image, image builds.
import { computed, ref } from 'vue'
import { useLoad } from '../load.js'
import { useKeys } from '../keys.js'
import { loadGroups, scope } from '../scope.js'
import DataTable from '../components/DataTable.vue'
import Scope from '../components/Scope.vue'
import TextPane from '../components/TextPane.vue'

const { data, loading, load } = useLoad((q) => `/packages${q}`)
loadGroups()
const sel = ref(null)
const rows = computed(() => (data.value?.packages || []).map((p) => ({
  ...p, key: `${p.owner}/${p.name}`, tags: p.row[1], release: p.row[2], issues: p.problems.join('; ') || 'ok',
})))
const columns = [
  { key: 'key', label: 'image', cls: (r) => (r.provider === 'gitlab' ? 'warn' : '') },
  { key: 'repo', label: 'repo', cls: () => 'dim' }, { key: 'tags', label: 'newest tags', cls: () => '' },
  { key: 'release', label: 'release image' }, { key: 'issues', label: 'problems' },
]
const current = computed(() => rows.value.find((r) => r.key === sel.value))
useKeys({ r: () => load(true) })
</script>

<template>
  <div class="view">
    <div class="toolbar"><Scope :groups="scope.groups" /><span v-if="data?.error" class="bad">{{ data.error }}</span></div>
    <div class="split">
      <div><DataTable v-model="sel" :columns="columns" :rows="rows" :empty="loading ? 'loading…' : 'no container images'" /></div>
      <div class="pad"><TextPane :text="current?.text" placeholder="pick an image" /></div>
    </div>
  </div>
</template>
