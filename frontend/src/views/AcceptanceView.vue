<!-- 分类器验收总览：九项证据、服务状态及白名单作业入口。 -->
<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import AdminShell from '../components/admin/AdminShell.vue'
import JobRow from '../components/admin/JobRow.vue'
import { useResource } from '../composables/useResource'
import type { AcceptanceOverview } from '../types/acceptance'

const { data, loading, error, load } = useResource<AcceptanceOverview>()
const refresh = () => load('/api/acceptance/overview')
const labels = { pass: '通过', fail: '不达标', missing: '缺产物' }
const evaluation = computed(() => {
  const block = data.value?.blocks.find((item) => item.key === 'eval')
  return !block || block.status === 'missing' ? '—' : block.headline.split(' · ')[0]
})
const jobsFor = (names: string[]) =>
  names.flatMap((name) => data.value?.jobs.filter((job) => job.name === name) ?? [])
onMounted(refresh)
</script>
<template>
  <AdminShell
    title="分类器验收总览"
    subtitle="九项实证全在页面上跑，不用回终端"
    :loading="loading"
    :error="error"
    @refresh="refresh"
  >
    <template v-if="data">
      <div class="gatebar">
        <div class="stat" :class="data.all_pass ? 'pass' : 'fail'">
          <span>闸门</span><b>{{ data.passed }} / {{ data.total }}</b>
        </div>
        <div class="stat" :class="data.classifier.online ? 'pass' : 'fail'">
          <span>分类器 :8110</span><b>{{ data.classifier.online ? '在线' : '离线' }}</b>
        </div>
        <div class="stat">
          <span>评测结论</span><b class="verdict">{{ evaluation }}</b>
        </div>
      </div>
      <div class="tip">
        口径：<b>通过</b>产物已生成且过线；<b>不达标</b>跑过但没过线，回头搞数据；<b>缺产物</b>还没跑过，点卡片里的按钮现场跑。页面读取与终端作业相同的产物，不在接口里重算。
      </div>
      <div class="cards">
        <article v-for="block in data.blocks" :key="block.key" class="gcard" :class="block.status">
          <div class="head">
            <div class="no">{{ block.no }}</div>
            <h3>{{ block.title }}</h3>
            <span class="pill" :class="block.status">{{ labels[block.status] }}</span
            ><span class="spacer" /><RouterLink v-if="block.page" class="btn sm" :to="block.page"
              >详情 →</RouterLink
            >
          </div>
          <div class="headline">{{ block.headline || '—' }}</div>
          <div class="note">{{ block.note }}</div>
          <div class="foot">
            <JobRow v-if="block.jobs.length" :jobs="jobsFor(block.jobs)" @finish="refresh" />
          </div>
        </article>
      </div>
    </template>
  </AdminShell>
</template>
<style scoped>
.cards {
  margin-top: 16px;
  display: grid;
  gap: 14px;
  grid-template-columns: repeat(auto-fill, minmax(min(340px, 100%), 1fr));
}
.gcard {
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
.no {
  border: 3px solid var(--ink);
  background: var(--paper);
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 13px;
  flex-shrink: 0;
}
.pass .no {
  background: var(--online);
}
.fail .no {
  background: var(--error);
  color: white;
}
h3 {
  font-size: 14px;
  margin: 0;
}
.headline {
  margin-top: 10px;
  font-size: 13px;
  line-height: 1.7;
  background: var(--paper);
  border: 2px solid var(--ink);
  padding: 7px 10px;
}
.note {
  margin-top: 7px;
  font-size: 12px;
  color: #6d6478;
  line-height: 1.7;
}
.foot {
  margin-top: auto;
  padding-top: 10px;
}
.verdict {
  font-size: 15px;
  line-height: 1.9;
}
</style>
