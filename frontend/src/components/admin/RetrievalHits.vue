<!-- 检索结果卡片：保留原始分与重排分、原文及章节来源，不计算新指标。 -->
<script setup lang="ts">
import type { SearchResult } from '../../types/kb'
import { score } from '../../utils/format'
defineProps<{ result: SearchResult }>()
</script>
<template>
  <div>
    <div class="feats">
      <span class="pill info">路线 {{ result.strategy }}</span
      ><span class="pill" :class="result.hits.length ? 'pass' : 'fail'"
        >召回 {{ result.hits.length }} 块</span
      >
    </div>
    <div v-if="!result.hits.length" class="miss">
      一条都没召回：库里可能还没有货，或者 pending 还没向量化
    </div>
    <div class="hits">
      <div
        v-for="(hit, index) in result.hits"
        :key="hit.id"
        class="hit"
        :class="{ top1: index === 0 }"
      >
        <div class="top">
          <span class="pill" :class="index === 0 ? 'pass' : 'info'">#{{ index + 1 }}</span
          ><span class="q">{{ hit.question }}</span>
        </div>
        <div class="a">{{ hit.answer }}</div>
        <div class="meta">
          id={{ hit.id }} · {{ hit.section_path || hit.category }} · {{ hit.content_type }} · 原始分
          {{ score(hit.score)
          }}<template v-if="hit.rerank_score != null">
            · 精排分 {{ score(hit.rerank_score) }}</template
          >
        </div>
      </div>
    </div>
  </div>
</template>
<style scoped>
.feats {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}
.hits {
  margin-top: 12px;
  display: grid;
  gap: 10px;
}
.hit {
  border: 3px solid var(--ink);
  background: var(--paper);
  padding: 9px 12px;
  font-size: 12.5px;
}
.top {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.q {
  font-weight: 700;
}
.a {
  margin-top: 6px;
  line-height: 1.75;
  white-space: pre-wrap;
}
.meta {
  margin-top: 6px;
  color: #6d6478;
  font-size: 11.5px;
}
.top1 {
  box-shadow: var(--shadow-sm);
  background: var(--cream);
}
</style>
