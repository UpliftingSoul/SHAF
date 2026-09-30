<!-- 类目问题列表：类目/页码保存在路由，参数变化自动取数并取消旧请求。 -->
<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import AdminShell from '../components/admin/AdminShell.vue'
import Paginator from '../components/admin/Paginator.vue'
import { useResource } from '../composables/useResource'
import { query } from '../api/http'
import { time } from '../utils/format'
import type { TopicDistribution, TopicQuestions } from '../types/topics'
const route = useRoute(),
  router = useRouter()
const label = computed(() => (typeof route.query.label === 'string' ? route.query.label : ''))
const page = computed(() => Math.max(1, Number.parseInt(String(route.query.page || '1'), 10) || 1))
const { data, error, loading, load } = useResource<TopicQuestions>()
const distribution = useResource<TopicDistribution>()
const picker = computed(() =>
  [...(distribution.data.value?.classes || [])].sort((a, b) => b.count - a.count),
)
const labels: Record<string, string> = {
  retrieval_low_conf: '检索置信度低',
  self_check: '生成自评不够答',
  user_feedback: '用户反馈没解决',
}
function go(target: string, targetPage = 1) {
  return router.push({ path: '/topics/questions', query: { label: target, page: targetPage } })
}
function refresh() {
  if (label.value)
    return load(`/api/topics/questions${query({ label: label.value, page: page.value, size: 10 })}`)
}
watch([label, page], refresh)
onMounted(() => {
  void refresh()
  void distribution.load('/api/topics/distribution')
})
</script>
<template>
  <AdminShell
    :title="label || '类目问题列表'"
    :subtitle="
      data?.label === label
        ? `分类器归到「${label}」的问题，共 ${data.total} 条`
        : '分类器归到这一类的问题，分页看全'
    "
    :loading="loading"
    :error="error"
    @refresh="refresh"
  >
    <template #actions><RouterLink class="btn" to="/topics">← 回主题分布</RouterLink></template>
    <div class="picker">
      <RouterLink
        v-for="topic in picker"
        :key="topic.label"
        :class="{ on: topic.label === label, zero: !topic.count }"
        :to="{ path: '/topics/questions', query: { label: topic.label, page: 1 } }"
        >{{ topic.label }}<span class="n">{{ topic.count }}</span></RouterLink
      >
    </div>
    <div v-if="!label" class="miss">没指定类目。回主题分布页，点某一类的名字进来。</div>
    <template v-else-if="data?.label === label"
      ><div v-if="!data.total" class="miss">
        这一类还没有归类到的问题。换个类目，或先跑一次旁路批量归类。
      </div>
      <section v-else class="panel">
        <h2>
          {{ label }}
          <span class="pill info"
            >{{ data.total }} 条 · 第 {{ data.page }}/{{ data.pages }} 页</span
          >
        </h2>
        <p class="lede">
          多标签问题会出现在每个命中类目下。问法取归并阶段产出的标准化问句，原话在下面一行。
        </p>
        <article v-for="item in data.items" :key="item.question_id" class="qrow">
          <div class="q">
            <span class="id">#{{ item.question_id }}</span
            >{{ item.text }}
          </div>
          <div class="meta">
            <span
              v-for="topic in item.labels"
              :key="topic"
              class="tag"
              :class="{ self: topic === label }"
              >{{ topic }}</span
            >
            <span
              >来源 <b>{{ labels[item.source] || item.source }}</b></span
            ><span
              >同义合并
              <b>{{
                item.occurrence_count ? `${item.occurrence_count} 条原话` : '未归并'
              }}</b></span
            ><span
              >审核 <b>{{ item.review_status || '未入队列' }}</b></span
            ><span
              >归类于 <b>{{ time(item.classified_at).slice(5, 16) }}</b></span
            >
          </div>
          <div
            v-if="item.normalized && item.raw_question && item.raw_question !== item.text"
            class="raw"
          >
            <span class="k">用户原话</span>{{ item.raw_question }}
          </div>
        </article>
        <Paginator
          :page="data.page"
          :pages="data.pages"
          :total="data.total"
          :size="data.size"
          :disabled="loading"
          @change="go(label, $event)"
        /></section
    ></template>
  </AdminShell>
</template>
<style scoped>
.qrow {
  border: 3px solid var(--ink);
  background: var(--paper);
  box-shadow: var(--shadow-sm);
  padding: 10px 13px;
  margin-top: 10px;
}
.q {
  font-size: 13.5px;
  font-weight: 700;
  line-height: 1.6;
}
.id {
  font-size: 11px;
  font-weight: 400;
  color: var(--muted);
  margin-right: 7px;
}
.meta {
  margin-top: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  font-size: 11.5px;
  color: #6d6478;
  align-items: center;
}
.meta b {
  color: var(--ink);
}
.tag {
  font-size: 11px;
  border: 2px solid var(--ink);
  background: var(--cream);
  padding: 0 5px;
}
.tag.self {
  background: var(--fur);
  font-weight: 700;
}
.raw {
  margin-top: 7px;
  font-size: 12px;
  color: #6d6478;
  line-height: 1.7;
}
.k {
  font-size: 10.5px;
  color: var(--muted);
  display: block;
}
.picker {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 16px;
}
.picker a {
  font-size: 12px;
  text-decoration: none;
  color: var(--ink);
  border: 3px solid var(--ink);
  background: var(--paper);
  padding: 3px 9px;
  box-shadow: 2px 2px 0 var(--ink);
}
.picker .on {
  background: var(--fur);
  font-weight: 700;
}
.n {
  color: var(--muted);
  margin-left: 5px;
}
.zero {
  opacity: 0.45;
}
</style>
