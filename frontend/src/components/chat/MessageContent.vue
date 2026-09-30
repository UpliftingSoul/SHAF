<!-- Markdown 与引用渲染：先消毒，再装饰文本节点；引用浮层只显示 Vue 转义文本。 -->
<script setup lang="ts">
import { computed, nextTick, onMounted, onScopeDispose, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import type { Citation } from '../../types/chat'

const props = defineProps<{ text: string; citations: Citation[]; decorate: boolean }>()
const container = ref<HTMLElement>()
const popup = ref<HTMLElement>()
const active = ref<Citation | null>(null)
const position = ref({ left: '12px', top: '12px', width: '320px' })
const html = computed(() => DOMPurify.sanitize(marked.parse(props.text, { async: false, breaks: true }) as string))
function close() { active.value = null }
function decorate() {
  if (!props.decorate || !container.value) return
  const sources = new Set(props.citations.map((item) => item.n))
  const walker = document.createTreeWalker(container.value, NodeFilter.SHOW_TEXT)
  const nodes: Text[] = []
  while (walker.nextNode()) nodes.push(walker.currentNode as Text)
  for (const node of nodes) {
    if (node.parentElement?.closest('code, pre, a, .cite')) continue
    const text = node.data
    const matches = [...text.matchAll(/\[(\d+)\]/g)].filter((match) => sources.has(Number(match[1])))
    if (!matches.length) continue
    const fragment = document.createDocumentFragment()
    let offset = 0
    for (const match of matches) {
      fragment.append(text.slice(offset, match.index))
      const button = document.createElement('button')
      button.className = 'cite'
      button.type = 'button'
      button.dataset.citation = match[1]
      button.textContent = match[0]
      button.setAttribute('aria-label', `查看来源 ${match[1]}`)
      fragment.append(button)
      offset = match.index! + match[0].length
    }
    fragment.append(text.slice(offset))
    node.replaceWith(fragment)
  }
}
async function click(event: MouseEvent) {
  const target = (event.target as Element).closest<HTMLElement>('[data-citation]')
  if (!target) return
  const source = props.citations.find((item) => item.n === Number(target.dataset.citation))
  if (!source) return
  active.value = source
  await nextTick()
  const rect = target.getBoundingClientRect()
  const width = Math.min(320, innerWidth - 24)
  const height = popup.value?.offsetHeight || 0
  const top = rect.bottom + 6 + height > innerHeight - 12 ? Math.max(12, rect.top - height - 6) : rect.bottom + 6
  position.value = { left: `${Math.max(12, Math.min(rect.left, innerWidth - width - 12))}px`, top: `${top}px`, width: `${width}px` }
}
function outside(event: MouseEvent) {
  const target = event.target as Element
  if (!popup.value?.contains(target) && !target.closest('[data-citation]')) close()
}
function escape(event: KeyboardEvent) { if (event.key === 'Escape') close() }
watch([html, () => props.decorate, () => props.citations], async () => { close(); await nextTick(); decorate() }, { flush: 'post' })
onMounted(() => {
  decorate()
  document.addEventListener('click', outside)
  document.addEventListener('keydown', escape)
  window.addEventListener('resize', close)
})
onScopeDispose(() => {
  document.removeEventListener('click', outside)
  document.removeEventListener('keydown', escape)
  window.removeEventListener('resize', close)
})
</script>

<template>
  <div ref="container" class="bubble-text" @click="click" v-html="html" />
  <Teleport to="body">
    <div v-if="active" ref="popup" class="cite-pop" :style="position">
      <div class="cite-pop-path">{{ active.section_path || '来源' }}</div>
      <div v-if="active.question" class="cite-pop-q">{{ active.question }}</div>
      <div class="cite-pop-a">{{ active.answer }}</div>
      <div class="cite-pop-meta">来源编号 [{{ active.n }}] · {{ active.content_type }}</div>
    </div>
  </Teleport>
</template>
