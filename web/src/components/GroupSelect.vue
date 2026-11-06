<script setup>
// The group filter: the groups of the defaults, then the orgs (GitHub) and GitLab groups of the repos,
// so every repo is in at least one choice, matched by a group or not.
import { computed } from 'vue'
import { ownerLabel, scope } from '../scope.js'
const props = defineProps({ groups: { type: Array, default: () => [] } })
const owners = computed(() => scope.owners.filter((o) => scope.provider === 'all' || o.startsWith('gitlab:') === (scope.provider === 'gitlab')))
</script>

<template>
  <select v-if="props.groups.length || owners.length" v-model="scope.group" aria-label="group">
    <option value="">all groups</option>
    <optgroup v-if="props.groups.length" label="groups">
      <option v-for="g in props.groups" :key="g" :value="g">{{ g }}</option>
    </optgroup>
    <optgroup v-if="owners.length" label="orgs">
      <option v-for="o in owners" :key="o" :value="o">{{ ownerLabel(o) }}</option>
    </optgroup>
  </select>
</template>
