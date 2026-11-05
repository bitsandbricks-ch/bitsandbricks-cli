<script setup>
// A week-by-week bar chart; `bad` bars stack on top in red.
import { computed } from 'vue'

const props = defineProps({ labels: Array, values: Array, bad: { type: Array, default: null }, title: String })
const top = computed(() => Math.max(1, ...(props.values || [0])))
</script>

<template>
  <div class="box">
    <h3>{{ title }}</h3>
    <div class="bars" role="img" :aria-label="`${title}: ${(values || []).join(', ')}`">
      <div v-for="(v, i) in values" :key="i" class="bar" :style="{ height: `${(100 * v) / top}%` }"
           :title="`${labels[i]}: ${v}${bad ? `, ${bad[i]} failed` : ''}`">
        <div v-if="bad && v" class="bar bad" :style="{ height: `${(100 * bad[i]) / v}%`, position: 'absolute', bottom: 0, left: 0, right: 0 }" />
      </div>
    </div>
    <div class="bars-l"><span v-for="(l, i) in labels" :key="i">{{ l.slice(-3) }}</span></div>
  </div>
</template>
