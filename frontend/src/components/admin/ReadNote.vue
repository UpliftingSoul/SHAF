<!-- 读图说明：产物注优先，缺失时使用页面说明；数字以文本片段加粗，拒绝 HTML 注入。 -->
<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ note?: string | null; fallback: string }>()
const parts = computed(() => {
  const text = props.note || props.fallback
  const result: { text: string; number: boolean }[] = []
  const pattern = /(?<![A-Za-z@_.\-\d])\d+(?:,\d{3})*(?:\.\d+)?%?(?![A-Za-z_])/g
  let offset = 0
  for (const match of text.matchAll(pattern)) {
    result.push(
      { text: text.slice(offset, match.index), number: false },
      { text: match[0], number: true },
    )
    offset = match.index! + match[0].length
  }
  result.push({ text: text.slice(offset), number: false })
  return result
})
</script>
<template>
  <div class="read">
    <template v-for="(part, index) in parts" :key="index"
      ><b v-if="part.number">{{ part.text }}</b
      ><template v-else>{{ part.text }}</template></template
    >
  </div>
</template>
<style scoped>
.read {
  margin-top: 12px;
  background: var(--paper);
  border: 2px dashed var(--ink);
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.8;
}
</style>
