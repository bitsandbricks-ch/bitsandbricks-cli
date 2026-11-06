<script setup>
// Which repos a list shows: GitHub, GitLab or both, and one group.
import { scope } from '../scope.js'
defineProps({ groups: { type: Array, default: () => [] } })
</script>

<template>
  <span class="row">
    <select v-model="scope.provider" aria-label="provider">
      <option value="all">github + gitlab</option>
      <option value="github">github</option>
      <option value="gitlab">gitlab</option>
    </select>
    <select v-if="groups.length" v-model="scope.group" aria-label="group">
      <option value="">all groups</option>
      <option v-for="g in groups" :key="g" :value="g">{{ g }}</option>
    </select>
  </span>
</template>
