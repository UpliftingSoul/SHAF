<!-- 观测与成本：保留三块报表、读图说明、阈值扫描及各自的作业重跑入口。 -->
<script setup lang="ts">
import { computed, onMounted } from 'vue'
import AdminShell from '../components/admin/AdminShell.vue'
import KpiRow from '../components/admin/KpiRow.vue'
import JobRow from '../components/admin/JobRow.vue'
import ReadNote from '../components/admin/ReadNote.vue'
import LineChart from '../components/admin/LineChart.vue'
import { useResource } from '../composables/useResource'
import { percent, score, time } from '../utils/format'
import type { ObservabilityData } from '../types/observability'
const { data, loading, error, load } = useResource<ObservabilityData>()
const labels: Record<string, string> = {
  recall_at_5: 'Recall@5',
  recall_at_10: 'Recall@10',
  mrr: 'MRR',
  faithfulness: 'Faithfulness',
  refusal_rate: '拒答率',
}
const thousands = (value?: number | null) => (value == null ? '—' : value.toLocaleString('en-US'))
const cost = computed(() => data.value?.cost),
  trend = computed(() => data.value?.trend),
  calibration = computed(() => data.value?.calibration)
const latest = computed(() => trend.value?.runs?.[0]),
  previous = computed(() => trend.value?.runs?.[1])
const drops = computed(() =>
  (trend.value?.metric_names || []).filter((name) => {
    const value = latest.value?.metrics[name],
      older = previous.value?.metrics[name]
    return value != null && older != null && value < older - 0.005
  }),
)
function arrow(value: number | null | undefined, older: number | null | undefined) {
  if (value == null || older == null) return ''
  return value - older > 0.005 ? '↑' : value - older < -0.005 ? '⚠↓' : '→'
}
const series = computed(() => [
  {
    label: '可答通过率',
    color: 'var(--online)',
    points: (calibration.value?.scan || []).map((row) => ({ x: row.t, y: row.pass_rate })),
  },
  {
    label: '应拒放行率',
    color: 'var(--error)',
    points: (calibration.value?.scan || []).map((row) => ({ x: row.t, y: row.leak_rate })),
  },
])
const markers = computed(() => {
  const rec = calibration.value?.recommended?.threshold
  if (rec == null) return []
  const result = [{ x: rec, label: '选定', color: 'var(--ink)', dashed: false }]
  if (calibration.value?.in_use !== rec)
    result.push({
      x: calibration.value!.in_use,
      label: '在用',
      color: 'var(--violet)',
      dashed: true,
    })
  return result
})
function refresh() {
  return load('/api/observability/overview')
}
onMounted(refresh)
</script>
<template>
  <AdminShell
    title="观测与成本"
    subtitle="钱花在哪类问题上 · 指标有没有劣化 · 兜底阈值怎么定的"
    :loading="loading"
    :error="error"
    @refresh="refresh"
  >
    <section v-if="cost" class="panel">
      <h2>意图成本账 <span class="pill info">数据源 Langfuse</span></h2>
      <p class="lede">按 trace 的 intent 标签分堆 token，优化成本先动最贵那条路。</p>
      <template v-if="cost.present && cost.rows?.length"
        ><KpiRow
          :items="[
            {
              label: '最烧钱意图',
              value: cost.top?.intent || '—',
              sub: `占 ${percent(cost.top?.share, 0)} · 单均 ${thousands(cost.top?.avg_tokens)} token`,
            },
            {
              label: '总 token',
              value: thousands(cost.total_tokens),
              sub: `${cost.total_requests} 次提问 · 近 ${cost.meta?.days} 天`,
            },
            { label: '意图路数', value: cost.rows.length, sub: '窗口内有 trace 的意图' },
            {
              label: '上次跑于',
              value: (cost.meta?.generated_at || '—').slice(5),
              sub: '页面只读产物，不查 Langfuse',
            },
          ]"
        />
        <div class="shares">
          <div v-for="row in cost.rows" :key="row.intent">
            <div class="sh-label">
              <div>
                <b>{{ row.intent }}</b
                ><span>{{ row.count }} 次 · 单均 {{ thousands(row.avg_tokens) }} token</span>
              </div>
              <b>{{ percent(row.share, 0) }} · {{ thousands(row.tokens) }}</b>
            </div>
            <div class="bar-track">
              <span
                class="bar-fill"
                :class="{ top: row.intent === cost.top?.intent }"
                :style="{ width: `${Math.max(1.5, row.share * 100)}%` }"
              />
            </div>
          </div>
        </div>
        <h3>明细</h3>
        <div class="scroll-x">
          <table class="tbl">
            <thead>
              <tr>
                <th>意图</th>
                <th>请求数</th>
                <th>总 token</th>
                <th>单均 token</th>
                <th>占比</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in cost.rows" :key="row.intent">
                <td>{{ row.intent }}</td>
                <td class="num">{{ row.count }}</td>
                <td class="num">{{ thousands(row.tokens) }}</td>
                <td class="num">{{ thousands(row.avg_tokens) }}</td>
                <td class="num">{{ percent(row.share, 0) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <ReadNote
          :note="cost.read_note"
          :fallback="`总账里 ${cost.top?.intent} 占 ${percent(cost.top?.share, 0)}，优化成本先检查这条链路的提示词、模型与调用次数。单均 token 与提问量应分别看。`"
        />
      </template>
      <div v-else class="miss">{{ cost.hint }}</div>
      <h3>在页面上重跑</h3>
      <JobRow :jobs="[cost.job]" @finish="refresh" />
    </section>
    <section v-if="trend" class="panel">
      <h2>评估趋势 <span class="pill info">数据源 eval_runs 表</span></h2>
      <p class="lede">每轮复用评估集并落表；新在上，指标与上一轮逐项对比防止知识飞轮劣化。</p>
      <div v-if="trend.status === 'error'" class="miss">趋势读不到：{{ trend.note }}</div>
      <template v-else-if="trend.present && latest"
        ><KpiRow
          :items="[
            { label: '已跑轮次', value: trend.runs?.length || 0, sub: '最多留最近十轮' },
            {
              label: '最近一轮',
              value: score(latest.metrics.faithfulness),
              sub: 'Faithfulness · 生成段有没有编',
            },
            {
              label: '检索 MRR',
              value: score(latest.metrics.mrr),
              sub:
                latest.metrics.recall_at_5 != null
                  ? `Recall@5 ${score(latest.metrics.recall_at_5)}`
                  : `Recall@10 ${score(latest.metrics.recall_at_10)}`,
            },
            {
              label: '对比上轮',
              value: previous ? drops.length : '—',
              unit: '项下滑',
              sub: previous
                ? drops.length
                  ? drops.map((name) => labels[name]).join('、') + ' 掉了'
                  : '可比指标持平或上涨'
                : '尚无上轮可比较',
            },
          ]" />
        <h3>最近十轮（新在上）</h3>
        <div class="scroll-x">
          <table class="tbl">
            <thead>
              <tr>
                <th>轮次</th>
                <th>时间</th>
                <th>触发</th>
                <th>评估集</th>
                <th v-for="name in trend.metric_names" :key="name">{{ labels[name] || name }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(run, index) in trend.runs" :key="run.id">
                <td>#{{ run.id }}</td>
                <td>{{ time(run.created_at).slice(5, 16) }}</td>
                <td>{{ run.triggered_by }}</td>
                <td class="num">{{ run.dataset_size }}</td>
                <td
                  v-for="name in trend.metric_names"
                  :key="name"
                  class="num"
                  :class="{
                    down: arrow(run.metrics[name], trend.runs?.[index + 1]?.metrics[name]) === '⚠↓',
                  }"
                >
                  {{ score(run.metrics[name]) }}
                  {{ arrow(run.metrics[name], trend.runs?.[index + 1]?.metrics[name]) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <ReadNote
          :note="trend.read_note"
          :fallback="
            previous
              ? drops.length
                ? `对比上一轮，${drops.map((name) => labels[name]).join('、')} 在下滑。检查最近写入的知识与评估现场。`
                : '可比指标没有退步，持续评估观察下一轮。缺失指标不参与涨跌判断。'
              : '当前只有一轮，还不能判断变化趋势。'
          "
      /></template>
      <div v-else class="miss">{{ trend.hint }}</div>
      <h3>在页面上重跑</h3>
      <JobRow :jobs="[trend.job]" @finish="refresh" />
    </section>
    <section v-if="calibration" class="panel">
      <h2>置信度阈值校准 <span class="pill info">评估集实跑</span></h2>
      <p class="lede">分别计算可答与应拒问题的证据置信度，扫描阈值，找出两拨题的分界线。</p>
      <template v-if="calibration.present && calibration.recommended"
        ><KpiRow
          :items="[
            {
              label: '推荐阈值',
              value: score(calibration.recommended.threshold, 2),
              sub: `Youden J ${score(calibration.recommended.youden_j)}`,
            },
            {
              label: '在用阈值',
              value: score(calibration.in_use, 2),
              sub: calibration.in_sync ? '与推荐一致' : '与推荐不一致，需要回填配置',
            },
            {
              label: '可答通过率',
              value: percent(calibration.recommended.pass_rate, 0),
              sub: '可答题通过证据闸的占比',
            },
            {
              label: '应拒放行率',
              value: percent(calibration.recommended.leak_rate, 0),
              sub: '库外问题被放进来答的占比',
            },
          ]"
        />
        <h3>证据置信度分布</h3>
        <div class="scroll-x">
          <table class="tbl">
            <thead>
              <tr>
                <th>题型</th>
                <th>题数</th>
                <th>最低</th>
                <th>p25</th>
                <th>中位</th>
                <th>p75</th>
                <th>最高</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="[name, key] in [
                  ['可答（A/B/C 桶）', 'answerable'],
                  ['应拒（D 桶）', 'absent'],
                ]"
                :key="key"
              >
                <td>{{ name }}</td>
                <td
                  v-for="field in ['n', 'min', 'p25', 'p50', 'p75', 'max']"
                  :key="field"
                  class="num"
                >
                  {{
                    field === 'n'
                      ? (calibration.distribution?.[key!]?.[field] ?? '—')
                      : score(calibration.distribution?.[key!]?.[field])
                  }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <h3>阈值扫描 <small>0.05 → 0.95，步进 0.01</small></h3>
        <LineChart
          :series="series"
          :min-x="0.05"
          :max-x="0.95"
          :ticks="[0.05, 0.2, 0.4, 0.6, 0.8, 0.95]"
          x-label="阈值 t"
          :markers="markers"
        />
        <ReadNote
          :note="calibration.read_note"
          :fallback="`推荐阈值 ${score(calibration.recommended.threshold, 2)} 下可答通过率 ${percent(calibration.recommended.pass_rate, 0)}，应拒放行率 ${percent(calibration.recommended.leak_rate, 0)}。调整阈值需要同时观察误拦和放行。`"
        />
      </template>
      <div v-else class="miss">{{ calibration.hint }}</div>
      <div class="split">
        <div class="note">
          <h3>置信度怎么算出来的</h3>
          <p>
            四信号加权：Top1 精排分 {{ calibration.weights.top1 }} / 有效证据数
            {{ calibration.weights.valid_count }} / Top1-Top2 分差
            {{ calibration.weights.margin }} / 关键条款命中
            {{ calibration.weights.key_clause }}。校准的是分界线。
          </p>
        </div>
        <div class="note">
          <h3>什么是「可答被误拦」</h3>
          <p>
            库里有答案，但这次召回的证据分低于线，于是走兜底入池；归并、审核补充更合适的知识后，同样问法才更容易召回。
          </p>
        </div>
      </div>
      <h3>在页面上重跑</h3>
      <JobRow :jobs="[calibration.job]" @finish="refresh" />
    </section>
  </AdminShell>
</template>
<style scoped>
h3 {
  font-size: 13px;
  margin: 16px 0 8px;
}
.shares {
  display: grid;
  gap: 10px;
}
.sh-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  margin-bottom: 5px;
}
.sh-label span {
  display: block;
  color: #6d6478;
  font-size: 11px;
  margin-top: 3px;
}
.bar-track {
  height: 18px;
  border: 2px solid var(--ink);
  background: var(--paper);
}
.bar-fill {
  display: block;
  height: 100%;
  background: var(--fur);
  border-right: 2px solid var(--ink);
}
.bar-fill.top {
  background: var(--coral);
}
.down {
  color: var(--error);
  font-weight: 700;
}
.split {
  margin-top: 14px;
}
.note {
  background: var(--paper);
  border: 2px solid var(--ink);
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.8;
}
.note h3 {
  margin: 0;
}
small {
  font-weight: 400;
  font-size: 11px;
  color: var(--muted);
}
</style>
