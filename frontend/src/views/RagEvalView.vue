<!-- RAG 评估入口：只读四策略报告，组织图表、生成评估、跨轮台账和重跑作业。 -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import AdminShell from '../components/admin/AdminShell.vue'
import KpiRow from '../components/admin/KpiRow.vue'
import JobRow from '../components/admin/JobRow.vue'
import ReadNote from '../components/admin/ReadNote.vue'
import RagChart from '../components/admin/RagChart.vue'
import RagGeneration from '../components/admin/RagGeneration.vue'
import FaithLedger from '../components/admin/FaithLedger.vue'
import { useResource } from '../composables/useResource'
import { buckets, strategies, metricValue, type RagMetric, type RagReport } from '../types/ragEval'
import { percent, score, time } from '../utils/format'

const { data, loading, error, load } = useResource<RagReport>()
const revision = ref(0)
const metric = ref<RagMetric>('MRR')
const metrics: RagMetric[] = ['MRR', 'Recall@5', '证据覆盖度']
const noteKeys: Record<RagMetric, string> = {
  MRR: 'rag_mrr',
  'Recall@5': 'rag_recall',
  证据覆盖度: 'rag_coverage',
  答案覆盖度: 'rag_answer_coverage',
}
const value = (m: RagMetric, strategy: string, bucket = 'overall') =>
  data.value ? metricValue(data.value, m, strategy, bucket) : null
const bestLabel = computed(
  () => strategies.find((strategy) => strategy.key === data.value?.best?.strategy)?.label || '—',
)
const kpis = computed(() => {
  const bm25 = value('MRR', 'bm25', 'C_colloquial'),
    rerank = value('MRR', 'hybrid_rerank', 'C_colloquial')
  const delta = bm25 == null || rerank == null ? null : rerank - bm25
  const generation = data.value?.generation
  return [
    {
      label: '最佳整体 MRR',
      value: score(data.value?.best?.mrr, 2),
      sub: bestLabel.value + ' · 四策略最高',
    },
    {
      label: '口语桶 MRR 提升',
      value: delta == null ? '—' : (delta >= 0 ? '+' : '') + score(delta, 2),
      sub: `纯 BM25 ${score(bm25, 2)} → 混合 + 重排 ${score(rerank, 2)}`,
    },
    {
      label: '答案覆盖度(重排)',
      value: score(value('答案覆盖度', 'hybrid_rerank'), 2),
      sub: generation ? '纯 BM25 ' + score(value('答案覆盖度', 'bm25'), 2) : '本次生成段未完成',
    },
    {
      label: '库外问题拒答',
      value: percent(generation?.refusal.rate, 0),
      sub: generation
        ? `${generation.refusal.correct} / ${generation.refusal.total} 正确拒答并落池`
        : '本次生成段未完成',
    },
  ]
})
const fallback = computed(() => {
  if (metric.value === 'MRR')
    return `型号桶纯向量 ${score(value('MRR', 'vector', 'B_model'), 2)}，口语桶纯 BM25 ${score(value('MRR', 'bm25', 'C_colloquial'), 2)}；总体最佳 ${bestLabel.value} ${score(data.value?.best?.mrr, 2)}。结合分桶成绩判断策略短板。`
  if (metric.value === 'Recall@5')
    return `召回率看前五条凑齐所需证据的程度。跨文档桶纯 BM25 ${score(value('Recall@5', 'bm25', 'E_multi'), 2)}，混合 + 重排 ${score(value('Recall@5', 'hybrid_rerank', 'E_multi'), 2)}。`
  return `证据覆盖度看召回的十条证据包含多少标准要点。纯 BM25 口语桶 ${score(value('证据覆盖度', 'bm25', 'C_colloquial'), 2)}，混合 + 重排 ${score(value('证据覆盖度', 'hybrid_rerank', 'C_colloquial'), 2)}。`
})
const notes = [
  [
    '三类查询，三种偏科',
    '型号桶包含易混型号族，口语桶更换了说法。用分桶成绩检查纯向量的型号辨别和 BM25 的关键词匹配能力。',
  ],
  ['融合 ≠ 重排', 'RRF 融合与重排是不同阶段；通过四策略对照检验重排是否将正确证据推到前面。'],
  [
    '确定性 vs LLM 裁判',
    '检索与证据覆盖度不依赖大模型；答案覆盖度和忠实度由裁判评估，可能有浮动。超时与失败跳过会明确展示。',
  ],
  [
    '两道生成前证据闸',
    '机械低分闸与语义自评闸都在生成前把关。证据不足时拒答，问题进入 low_confidence_questions。',
  ],
]
async function refresh() {
  await load('/api/rag-eval/overview')
  revision.value++
}
onMounted(refresh)
</script>
<template>
  <AdminShell
    title="RAG 评估"
    subtitle="四策略对照 · 检索排序 / 证据覆盖 / 端到端答案，一次跑齐"
    :loading="loading"
    :error="error"
    @refresh="refresh"
  >
    <template v-if="data">
      <div v-if="!data.present" class="miss">{{ data.hint }}</div>
      <template v-else>
        <div class="gatebar">
          <div class="stat">
            <span>评估集</span><b>{{ data.meta?.n_samples ?? '—' }} 题</b>
          </div>
          <div class="stat">
            <span>知识库</span><b>{{ data.meta?.kb_chunks ?? '—' }} 块</b>
          </div>
          <div class="stat"><span>嵌入 / 重排</span><b>bge-m3 · reranker-v2-m3</b></div>
          <div class="stat">
            <span>裁判模型</span><b>{{ data.meta?.chat_model || '—' }}</b>
          </div>
          <div class="stat">
            <span>上次跑于</span><b>{{ data.meta?.generated_at || '—' }}</b>
          </div>
        </div>
        <KpiRow :items="kpis" />
        <section class="panel">
          <h2>检索质量(确定性) <span class="pill info">不依赖大模型 · 可复现</span></h2>
          <p class="lede">
            MRR 看排序是否靠前，Recall@5
            看前五条证据凑齐程度；证据覆盖度看召回十条包含的标准要点。库外 D_absent
            桶没有标准答案，在生成段评估拒答。
          </p>
          <div class="metricbar">
            <div class="subhead">{{ metric }} <span>越高越好 · 0–1</span></div>
            <div class="seg">
              <button
                v-for="item in metrics"
                :key="item"
                :aria-pressed="metric === item"
                @click="metric = item"
              >
                {{ item }}
              </button>
            </div>
          </div>
          <RagChart :report="data" :metric="metric" /><ReadNote
            :note="data.read_notes?.[noteKeys[metric]]"
            :fallback="fallback"
          />
        </section>
        <RagGeneration :report="data" />
        <section class="panel">
          <h2>完整数据</h2>
          <p class="lede">分桶 MRR 与总体覆盖度汇总。带底色的行是总体 MRR 最佳策略。</p>
          <div class="scroll-x">
            <table class="tbl">
              <thead>
                <tr>
                  <th>策略</th>
                  <th v-for="bucket in buckets" :key="bucket.key">
                    {{ bucket.label.replace('类', '') }} MRR
                  </th>
                  <th>证据覆盖</th>
                  <th>答案覆盖</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="strategy in strategies"
                  :key="strategy.key"
                  :class="{ win: strategy.key === data.best?.strategy }"
                >
                  <th><i :style="{ background: strategy.color }" />{{ strategy.label }}</th>
                  <td v-for="bucket in buckets" :key="bucket.key" class="num">
                    {{ score(value('MRR', strategy.key, bucket.key), 2) }}
                  </td>
                  <td class="num">{{ score(value('证据覆盖度', strategy.key), 2) }}</td>
                  <td class="num">{{ score(value('答案覆盖度', strategy.key), 2) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="tip">
            <b>D_absent</b> 库外问题没有标准答案，不参与上表。{{
              data.generation
                ? `上线管线正确拒答 ${data.generation.refusal.correct} / ${data.generation.refusal.total} = ${percent(data.generation.refusal.rate, 0)}。`
                : '拒答率要等生成段完成。'
            }}
          </div>
          <template v-if="data.generation"
            ><details v-if="data.generation.faithfulness_cases?.length" class="faith">
              <summary>
                编造个案
                <span class="pill sev-中">{{ data.generation.faithfulness_cases.length }} 例</span>
                · 点开看问题 / 生成答案 / 裁判理由
              </summary>
              <div class="fcbody">
                <p class="lead">
                  上线管线生成答案被忠实度裁判判为有证据未支撑的内容，需人工核对并回补知识或调整判据。
                </p>
                <article
                  v-for="item in data.generation.faithfulness_cases"
                  :key="item.id"
                  class="fcase"
                >
                  <div class="fh">
                    <span class="fid">{{ item.id }}</span
                    ><b>{{ item.query }}</b
                    ><small
                      >{{
                        buckets.find((bucket) => bucket.key === item.bucket)?.label || item.bucket
                      }}桶</small
                    >
                  </div>
                  <div class="frow"><span class="k">裁判理由</span>{{ item.reason || '—' }}</div>
                  <div class="frow">
                    <span class="k">生成答案(原文)</span>
                    <div class="ans">{{ item.answer }}</div>
                  </div>
                </article>
              </div>
            </details>
            <div v-else class="faith-none">
              ✓ 本轮上线管线的生成答案没有被判「编造」的个案。
            </div></template
          >
        </section>
        <FaithLedger :revision="revision" />
      </template>
      <section class="panel tight">
        <h2>就地重跑 <span class="pill missing">分钟级</span></h2>
        <p class="lede">运行原来的 make eval-rag，四策略各跑检索与裁判评估。完成后读取新产物。</p>
        <JobRow :jobs="data.job.specs" @finish="refresh" />
        <p class="artifact">
          产物 {{ data.job.artifacts.json.path }}（{{
            data.job.artifacts.json.present
              ? '写于 ' + time(data.job.artifacts.json.mtime)
              : '还没有'
          }}）
        </p>
      </section>
      <section v-if="data.present" class="panel">
        <h2>怎么读这份报告</h2>
        <div class="notes">
          <div v-for="[title, text] in notes" :key="title" class="note">
            <h3>{{ title }}</h3>
            <p>{{ text }}</p>
          </div>
        </div>
      </section>
    </template>
  </AdminShell>
</template>
<style scoped>
.gatebar .stat b {
  font-size: 14px;
  line-height: 1.8;
  overflow-wrap: anywhere;
}
.metricbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.subhead {
  font-size: 13px;
  font-weight: 700;
}
.subhead span {
  color: var(--muted);
  font-weight: 400;
  font-size: 11.5px;
}
.seg {
  display: inline-flex;
  border: 3px solid var(--ink);
  background: var(--paper);
}
.seg button {
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  border: 0;
  background: transparent;
  padding: 4px 11px;
  border-right: 3px solid var(--ink);
}
.seg button:last-child {
  border-right: 0;
}
.seg button[aria-pressed='true'] {
  background: var(--fur);
  font-weight: 700;
}
.tbl tbody th {
  background: var(--paper);
  font-weight: 700;
}
.tbl th i {
  width: 10px;
  height: 10px;
  border: 2px solid var(--ink);
  display: inline-block;
  margin-right: 6px;
}
.tbl tr.win td,
.tbl tr.win th {
  background: #fff0d6;
}
.faith {
  margin-top: 12px;
  border: 3px solid var(--ink);
  background: var(--paper);
}
.faith summary {
  cursor: pointer;
  padding: 8px 12px;
  font-size: 12.5px;
  font-weight: 700;
  background: var(--cream);
}
.fcbody {
  padding: 4px 12px 12px;
}
.lead {
  font-size: 12px;
  color: #6d6478;
  line-height: 1.8;
}
.fcase {
  border: 2px solid var(--ink);
  background: var(--cream);
  padding: 9px 11px;
  margin-top: 10px;
}
.fh {
  display: flex;
  gap: 8px;
  align-items: baseline;
  flex-wrap: wrap;
  font-size: 13px;
}
.fid {
  font-size: 11px;
  border: 2px solid var(--ink);
  background: var(--fur);
  padding: 0 5px;
}
.fh small,
.k {
  font-size: 10.5px;
  color: var(--muted);
}
.frow {
  font-size: 12.5px;
  line-height: 1.75;
  margin-top: 7px;
}
.k {
  display: block;
}
.ans {
  white-space: pre-wrap;
  border: 2px solid var(--ink);
  background: var(--paper);
  padding: 8px 10px;
  margin-top: 3px;
}
.faith-none {
  margin-top: 12px;
  border: 3px solid var(--ink);
  background: #e8fae9;
  padding: 10px 13px;
  font-size: 12.5px;
}
.artifact {
  font-size: 11.5px;
  color: var(--muted);
  overflow-wrap: anywhere;
}
.notes {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.note {
  border: 3px solid var(--ink);
  background: var(--paper);
  padding: 10px 13px;
}
.note h3 {
  font-size: 12.5px;
  margin: 0 0 5px;
}
.note p {
  font-size: 12px;
  color: #6d6478;
  line-height: 1.8;
  margin: 0;
}
@media (max-width: 820px) {
  .notes {
    grid-template-columns: 1fr;
  }
}
</style>
