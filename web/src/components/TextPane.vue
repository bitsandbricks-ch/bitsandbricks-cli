<script setup>
// Command output and details: plain text with clickable links (no v-html anywhere).
import { computed } from 'vue'
import { linkify, stateClass } from '../ui.js'

const props = defineProps({ text: { type: String, default: '' }, placeholder: { type: String, default: '' } })
const lines = computed(() => String(props.text || '').split('\n').map((l) => ({
  parts: linkify(l),
  cls: /^\s*(error|.*\bnot merged\b)/i.test(l) ? 'bad' : /^\s*-\s/.test(l) ? 'warn' : /^==/.test(l) ? 'accent'
    : /\[dry-run\]/.test(l) ? 'dim' : '',
})))
</script>

<template>
  <pre v-if="text" class="text"><template v-for="(l, i) in lines" :key="i"><span :class="l.cls"><template
    v-for="(p, j) in l.parts" :key="j"><a v-if="p.href" :href="p.href" target="_blank" rel="noopener noreferrer"
    :title="p.href">{{ p.label }}</a><template v-else>{{ p.text }}</template></template></span>
</template></pre>
  <div v-else class="empty">{{ placeholder }}</div>
</template>
