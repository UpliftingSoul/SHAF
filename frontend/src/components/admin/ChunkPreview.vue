<!-- 切块预览：展示与后端相同的块、查重及切分特性，不在浏览器重复切块。 -->
<script setup lang="ts">
import type { Preview } from '../../types/kb'
defineProps<{ preview: Preview }>()
</script>
<template><div class="chunk-preview"><div class="feats">
  <span class="pill info">来源 {{ preview.source }}</span><span class="pill info">共 {{ preview.total }} 块 / {{ preview.features.sections }} 节</span>
  <span class="pill" :class="preview.features.table_split ? 'pass' : 'missing'">表格按行拆{{ preview.features.table_split ? '已' : '未' }}触发</span>
  <span class="pill" :class="preview.features.overlap ? 'pass' : 'missing'">句末重叠{{ preview.features.overlap ? '已触发' : '未触发（单节未超 400 字）' }}</span>
  <span class="pill" :class="preview.key_clause ? 'pass' : 'missing'">关键条款 {{ preview.key_clause }} 块</span>
  <span class="pill" :class="!preview.dedup_known ? 'missing' : preview.duplicates ? 'fail' : 'pass'">{{ !preview.dedup_known ? '查重未知（MySQL 读不到）' : preview.duplicates ? `库里已有 ${preview.duplicates} 块，入库会跳过` : '无重复，可入库' }}</span>
</div><div class="scroll-x"><table class="tbl"><thead><tr><th>#</th><th>节（section_path）</th><th>问法 questions</th><th>正文 answer</th><th>字数</th><th>标记</th></tr></thead><tbody>
  <tr v-for="chunk in preview.chunks" :key="chunk.seq" :class="{ bad: chunk.duplicate }"><td class="num">{{ chunk.seq }}</td><td>{{ chunk.section_path }}</td>
    <td>{{ chunk.questions }}</td><td><div class="ans">{{ chunk.answer }}</div></td><td class="num">{{ chunk.chars }}</td><td class="mark">
      <span v-if="chunk.is_key_clause" class="pill fail">关键条款</span><span v-if="chunk.is_table" class="pill info">表格块</span><span v-if="chunk.duplicate" class="pill missing">重复</span>
    </td></tr>
</tbody></table></div></div></template>
<style scoped>
.feats { display:flex; gap:8px; flex-wrap:wrap; margin:10px 0 12px; }.ans { white-space:pre-wrap; word-break:break-word; max-height:128px; overflow:auto; font-size:12px; line-height:1.7; }.mark { white-space:nowrap; }
</style>
