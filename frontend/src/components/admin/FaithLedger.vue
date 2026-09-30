<!-- 跨轮编造台账：独立取数和分页，本轮幻觉率与累计台账明确分开。 -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import FaithCaseCard from './FaithCaseCard.vue'
import Paginator from './Paginator.vue'
import { useResource } from '../../composables/useResource'
import { query } from '../../api/http'
import type { FaithLedger, FaithStatus } from '../../types/ragEval'
import { percent } from '../../utils/format'
const props = defineProps<{ revision: number }>()
const status = ref<FaithStatus | ''>('未解决')
const page = ref(1)
const { data, loading, error, load } = useResource<FaithLedger>()
const hallucination = computed(() => data.value?.hallucination)
const statuses: (FaithStatus | '')[] = ['未解决', '已解决', '无需解决', '']
const total = computed(() =>
  data.value ? Object.values(data.value.counts).reduce((sum, n) => sum + n, 0) : 0,
)
const refresh = () =>
  load(
    '/api/rag-eval/faith-cases' +
      query({ status: status.value || null, page: page.value, size: 5 }),
  )
function filter(next: FaithStatus | '') {
  status.value = next
  page.value = 1
}
watch([status, page, () => props.revision], refresh, { immediate: true })
</script>
<template>
  <section class="panel">
    <h2>编造个案台账 <span class="pill info">跨轮累计 · 可处置</span></h2>
    <p class="lede">
      历轮个案按题号累计；再次被判编造会增加次数，处置后再次出现会退回未解决并标复发。证据原文用于追溯当时模型输入。
    </p>
    <div v-if="error" class="miss" role="alert">
      台账取数失败：{{ error }}（这张表要先应用 sql/ch04-faith-ddl.sql）
      <button class="btn sm" @click="refresh">重试</button>
    </div>
    <template v-if="data && hallucination"
      ><div class="halluc">
        <div class="hcard main">
          <div class="hl">本轮确认幻觉率(人工过目后算的真账)</div>
          <div class="hv">{{ percent(hallucination.confirmed_rate) }}</div>
          <div class="hs">
            {{ hallucination.confirmed }} / {{ hallucination.evaluated ?? '—' }} 道评上的题（编造
            {{ hallucination.cases_confirmed }} 条 + 该拒没拒
            {{ hallucination.refusal_missed }} 条）。编造只算确认真编了并标已解决的；{{
              hallucination.pending
                ? `还有 ${hallucination.pending} 条没过目，当前是下界。`
                : '本轮判出的都过目了，账已结清。'
            }}库外题该拒没拒无需人工确认。
          </div>
        </div>
        <div class="hcard">
          <div class="hl">本轮裁判判出率(线索量，别当结论)</div>
          <div class="hv">{{ percent(hallucination.judged_rate) }}</div>
          <div class="hs">
            {{ hallucination.judged }} / {{ hallucination.evaluated ?? '—' }} 道（编造
            {{ hallucination.cases_judged }} 条 + 该拒没拒
            {{ hallucination.refusal_missed }} 条）；{{
              hallucination.dismissed
            }}
            条被人工判为无需解决。分母 = 可作答 {{ hallucination.graded ?? '—' }} + 库外
            {{ hallucination.absent ?? '—' }} 道。台账累计
            {{ hallucination.ledger.total }} 条（未解决 {{ hallucination.ledger['未解决'] }} ·
            已解决 {{ hallucination.ledger['已解决'] }} · 无需解决
            {{ hallucination.ledger['无需解决'] }}），不能拿累计条数除以一轮题量。
          </div>
        </div>
      </div>
      <div class="ledger-bar">
        <button
          v-for="item in statuses"
          :key="item"
          class="tab"
          :class="{ on: status === item }"
          :disabled="loading"
          @click="filter(item)"
        >
          {{ item || '全部' }}（{{ item ? data.counts[item] : total }}）
        </button>
      </div>
      <div v-if="!data.items.length" class="empty">
        {{
          total
            ? '这个状态下暂时没有个案。'
            : '台账还是空的——跑一轮 make eval-rag，判出的编造个案会自动写进来。'
        }}
      </div>
      <template v-else
        ><FaithCaseCard
          v-for="item in data.items"
          :key="item.id + ':' + item.status + ':' + item.last_seen_at"
          :item="item"
          @changed="refresh" /><Paginator
          :page="data.page"
          :pages="data.pages"
          :total="data.total"
          :size="data.size"
          :disabled="loading"
          @change="page = $event" /></template
    ></template>
  </section>
</template>
<style scoped>
.halluc {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 12px;
}
.hcard {
  border: 3px solid var(--ink);
  background: var(--paper);
  padding: 9px 12px 11px;
}
.main {
  background: var(--cream);
  box-shadow: var(--shadow-sm);
}
.hl {
  font-size: 11.5px;
  color: var(--muted);
}
.hv {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.45;
}
.hs {
  font-size: 11.5px;
  color: #6d6478;
  line-height: 1.7;
}
.ledger-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin: 12px 0 2px;
}
.tab {
  cursor: pointer;
  border: 2px solid var(--ink);
  background: var(--paper);
  padding: 4px 11px;
  font: inherit;
  font-size: 11.5px;
}
.tab.on {
  background: var(--fur);
  font-weight: 700;
}
.tab:disabled {
  opacity: 0.5;
}
.empty {
  margin-top: 12px;
  border: 3px solid var(--ink);
  background: #e8fae9;
  padding: 10px 13px;
  font-size: 12.5px;
}
@media (max-width: 720px) {
  .halluc {
    grid-template-columns: 1fr;
  }
}
</style>
