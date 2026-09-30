<!-- 主题分布：按问题量排序，展开三条样例；类目链接进入服务端分页列表。 -->
<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import AdminShell from '../components/admin/AdminShell.vue'
import { useResource } from '../composables/useResource'
import type { TopicDistribution } from '../types/topics'
const { data, error, loading, load } = useResource<TopicDistribution>()
const classes = computed(() => [...(data.value?.classes || [])].sort((a, b) => b.count - a.count))
const max = computed(() => Math.max(1, ...classes.value.map((item) => item.count)))
const latest = computed(() => {
  if (!data.value?.latest) return '—'
  const iso = data.value.latest
  return new Date(/[Zz]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
})
function link(label: string) {
  return { path: '/topics/questions', query: { label, page: 1 } }
}
function refresh() {
  return load('/api/topics/distribution')
}
onMounted(refresh)
</script>
<template>
  <AdminShell
    title="主题分布"
    subtitle="低置信度问题 → 分类器旁路归类 → 哪类堆得多，先补哪块知识"
    :loading="loading"
    :error="error"
    @refresh="refresh"
  >
    <div class="stats">
      <div class="stat">
        已归类问题<b>{{ data?.total ?? '—' }}</b>
      </div>
      <div class="stat">
        最近归类<b class="latest">{{ latest }}</b>
      </div>
      <div class="stat">
        命中类目数<b>{{ classes.filter((item) => item.count > 0).length }} / 17</b>
      </div>
    </div>
    <section class="chart">
      <h2>17 类权威类目 · 问题量（降序，点行展开样例，点类目名看全部）</h2>
      <details
        v-for="(topic, index) in classes"
        :key="topic.label"
        class="row"
        :class="{ zero: !topic.count, top1: index === 0 && topic.count > 0 }"
      >
        <summary>
          <span class="name"
            ><RouterLink v-if="topic.count" :to="link(topic.label)" @click.stop>{{
              topic.label
            }}</RouterLink
            ><template v-else>{{ topic.label }}</template></span
          >
          <span class="track"
            ><span
              class="bar"
              :style="{ width: `${Math.round((topic.count / max) * 100)}%` }" /></span
          ><span class="count">{{ topic.count }}</span>
        </summary>
        <div class="samples">
          <template v-if="topic.samples.length"
            ><div v-for="sample in topic.samples" :key="sample">· {{ sample }}</div>
            <RouterLink class="all" :to="link(topic.label)"
              >查看全部 {{ topic.count }} 条 →</RouterLink
            ></template
          >
          <div v-else class="none">暂无样例</div>
        </div>
      </details>
      <div class="hint">
        数据来自 topic_classifications（旁路批量归类）；多标签问题计入每个命中类目。
      </div>
    </section>
  </AdminShell>
</template>
<style scoped>
:deep(.wrap) {
  max-width: 1080px;
}
.stats {
  margin-top: 16px;
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
}
.stat {
  background: var(--paper);
  border: 3px solid var(--ink);
  box-shadow: var(--shadow-sm);
  padding: 10px 16px;
  font-size: 13px;
}
.stat b {
  font-size: 20px;
  display: block;
}
.stat .latest {
  font-size: 14px;
  line-height: 28px;
}
.chart {
  margin-top: 16px;
  background: var(--cream);
  border: 4px solid var(--ink);
  box-shadow: var(--shadow);
  padding: 16px 18px;
}
.chart h2 {
  font-size: 14px;
  margin: 0 0 12px;
}
.row {
  margin: 6px 0;
}
.row summary {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  list-style: none;
}
.row summary::-webkit-details-marker {
  display: none;
}
.name {
  width: 6.5em;
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 700;
  text-align: right;
}
.name a {
  color: inherit;
  text-decoration: none;
  border-bottom: 2px solid var(--fur);
}
.name a:hover {
  background: var(--fur);
}
.track {
  flex: 1;
  background: var(--paper);
  border: 2px solid var(--ink);
  height: 22px;
  position: relative;
}
.bar {
  display: block;
  height: 100%;
  background: var(--fur);
  border-right: 2px solid var(--ink);
  min-width: 2px;
  transition: width 0.4s;
}
.zero .bar {
  display: none;
}
.zero {
  opacity: 0.45;
}
.count {
  width: 3em;
  flex-shrink: 0;
  font-weight: 700;
  font-size: 13px;
}
.top1 .bar {
  background: var(--coral);
}
.samples {
  margin: 6px 0 10px calc(6.5em + 10px);
  font-size: 12.5px;
  color: var(--ink);
  background: var(--paper);
  border: 2px dashed var(--ink);
  padding: 8px 12px;
}
.samples div {
  margin: 2px 0;
}
.samples .all {
  display: inline-block;
  margin-top: 7px;
  font-size: 12px;
  text-decoration: none;
  color: var(--ink);
  border: 3px solid var(--ink);
  background: var(--cream);
  padding: 2px 9px;
  box-shadow: 2px 2px 0 var(--ink);
  font-weight: 700;
}
.none,
.hint {
  color: var(--muted);
}
.hint {
  margin-top: 10px;
  font-size: 12px;
}
</style>
