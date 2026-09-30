<!-- 后台聚合首页：显示各模块 API 提供的状态、结论和入口，不重复计算业务指标。 -->
<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import AdminShell from '../components/admin/AdminShell.vue'
import { useResource } from '../composables/useResource'
interface ModuleCard {
  key: string
  title: string
  page: string
  lede: string
  status: 'ok' | 'attention' | 'missing' | 'error'
  headline: string
  metrics: { label: string; value: string | number | null }[]
  note: string | null
}
const { data, loading, error, load } = useResource<{ modules: ModuleCard[] }>()
const labels = { ok: '正常', attention: '要干活', missing: '没数据', error: '读数失败' }
function refresh() {
  return load('/api/admin/overview')
}
onMounted(refresh)
</script>
<template>
  <AdminShell
    title="后台管理"
    subtitle="知识库、检索评估、飞轮、分类器，几块后台都在这里，不用回终端"
    :loading="loading"
    :error="error"
    @refresh="refresh"
  >
    <div class="tip">
      口径：<b>正常</b>这块是活的且没有待办；<b>要干活</b>有待办或两边数对不上；<b>没数据</b>还没跑过；<b>读数失败</b>这块的依赖没起，只影响它自己那张卡。
    </div>
    <div class="mods">
      <article v-for="module in data?.modules" :key="module.key" class="mod">
        <div class="head">
          <h3>{{ module.title }}</h3>
          <span class="spacer" /><span class="pill" :class="module.status">{{
            labels[module.status]
          }}</span>
        </div>
        <p class="lede">{{ module.lede }}</p>
        <div class="headline">{{ module.headline }}</div>
        <div v-if="module.metrics.length" class="kpis">
          <div v-for="metric in module.metrics" :key="metric.label" class="kpi">
            <b>{{ metric.value ?? '—' }}</b
            >{{ metric.label }}
          </div>
        </div>
        <div v-if="module.note" class="note">{{ module.note }}</div>
        <div class="foot">
          <RouterLink class="btn go" :to="module.page">进入 {{ module.title }} →</RouterLink>
        </div>
      </article>
    </div>
  </AdminShell>
</template>
<style scoped>
.mods {
  margin-top: 16px;
  display: grid;
  gap: 14px;
  grid-template-columns: repeat(auto-fill, minmax(min(340px, 100%), 1fr));
}
.mod {
  background: var(--cream);
  border: 4px solid var(--ink);
  box-shadow: var(--shadow);
  padding: 12px 14px 14px;
  display: flex;
  flex-direction: column;
}
.head {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
}
.mod h3 {
  font-size: 15px;
  margin: 0;
}
.lede {
  color: #6d6478;
  font-size: 12px;
  line-height: 1.7;
  margin: 7px 0 0;
}
.headline {
  margin: 10px 0 0;
  font-size: 13px;
  line-height: 1.7;
  background: var(--paper);
  border: 2px solid var(--ink);
  padding: 7px 10px;
}
.kpis {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
}
.kpi {
  border: 2px solid var(--ink);
  background: var(--paper);
  padding: 4px 9px;
  font-size: 11.5px;
  min-width: 76px;
}
.kpi b {
  display: block;
  font-size: 17px;
  line-height: 1.4;
}
.note {
  margin-top: 8px;
  font-size: 11.5px;
  color: #6d6478;
  line-height: 1.7;
}
.foot {
  margin-top: auto;
  padding-top: 12px;
}
.pill.ok {
  background: var(--online);
}
.pill.attention {
  background: var(--fur);
}
.pill.error {
  background: var(--error);
  color: #fff;
}
</style>
