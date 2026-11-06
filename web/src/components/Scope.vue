<script setup>
// Which repos a list shows: GitHub, GitLab or both, and one group (of the defaults, or an org).
import { scope } from '../scope.js'
import GroupSelect from './GroupSelect.vue'
defineProps({ groups: { type: Array, default: () => [] } })
</script>

<template>
  <span class="row">
    <select v-model="scope.provider" aria-label="provider">
      <option value="all">github + gitlab</option>
      <option value="github">github</option>
      <option value="gitlab">gitlab</option>
    </select>
    <GroupSelect :groups="groups" />
  </span>
</template>
