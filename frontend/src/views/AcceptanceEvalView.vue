<!-- 分类器评测详情：总分、每类红线、阈值扫描、混淆矩阵及单句试分类。 -->
<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import AdminShell from '../components/admin/AdminShell.vue'
import JobRow from '../components/admin/JobRow.vue'
import ScoreCell from '../components/admin/ScoreCell.vue'
import ClassifierTry from '../components/admin/ClassifierTry.vue'
import { useResource } from '../composables/useResource'
import type { AcceptanceEvaluation } from '../types/acceptance'
import type { JobSpec } from '../types/jobs'
import { score, time } from '../utils/format'

const report = useResource<AcceptanceEvaluation>()
const jobs = useResource<{ jobs: JobSpec[] }>()
const { data, loading, error } = report
const evaluation = computed(() => data.value?.eval)
const scan = computed(() => {
  const raw = data.value?.scan
  if (!raw) return undefined
  // 现有扫描产物字段是 in_use；兼容旧页面预期的别名，避免在用标记丢失。
  return {
    ...raw,
    in_use_threshold: raw.in_use_threshold ?? raw.in_use ?? data.value?.threshold_in_use,
  }
})
const gap = computed(() =>
  evaluation.value?.micro && evaluation.value.macro
    ? Math.abs(evaluation.value.micro.f1 - evaluation.value.macro.f1)
    : null,
)
const jobsFor = (names: string[]) =>
  names.flatMap((name) => jobs.data.value?.jobs.filter((job) => job.name === name) ?? [])
const close = (a?: number | null, b?: number | null) =>
  a != null && b != null && Math.abs(a - b) < 1e-9
const scanWidth = (value: number) => {
  const values = scan.value?.scan?.map((row) => row.micro_f1) ?? []
  const low = Math.min(...values),
    high = Math.max(...values)
  return `${high > low ? 8 + (92 * (value - low)) / (high - low) : 100}%`
}
const matrixCells = [
  { key: 'tn', label: 'TN 不该打' },
  { key: 'fp', label: 'FP 冤枉' },
  { key: 'fn', label: 'FN 放跑' },
  { key: 'tp', label: 'TP 打对' },
] as const
const refresh = () => Promise.all([report.load('/api/acceptance/eval'), jobs.load('/api/jobs')])
onMounted(refresh)
</script>
<template>
  <AdminShell
    title="分类器评测验收"
    subtitle="总分 / 每类红线 / 判定阈值 / 混淆矩阵"
    :loading="loading"
    :error="error || jobs.error.value"
    @refresh="refresh"
  >
    <template v-if="data && evaluation && scan">
      <div class="gatebar">
        <template v-if="evaluation.present"
          ><div class="stat">
            <span>测试集</span><b>{{ evaluation.test_size }} 条</b>
          </div>
          <div class="stat">
            <span>判定阈值</span><b>{{ evaluation.threshold ?? '—' }}</b>
          </div>
          <div class="stat" :class="{ pass: evaluation.red_line_passed }">
            <span>micro-F1</span><b>{{ score(evaluation.micro?.f1) }}</b>
          </div>
          <div class="stat">
            <span>macro-F1</span><b>{{ score(evaluation.macro?.f1) }}</b>
          </div>
          <div class="stat" :class="evaluation.red_line_passed ? 'pass' : 'fail'">
            <span>容错红线</span><b>{{ evaluation.red_line_passed ? '全过' : '有不达标' }}</b>
          </div>
          <div class="stat">
            <span>评测于</span><b class="date">{{ time(evaluation.ran_at).slice(5, 16) }}</b>
          </div></template
        >
        <div v-else class="stat fail"><span>评测产物</span><b>未生成</b></div>
      </div>
      <!-- 总分只是报告摘要，不在前端重新计算评测。 -->
      <section class="panel">
        <h2>总分：micro 与 macro 一起看</h2>
        <p class="lede">
          micro 把 17 类所有对错混进一个桶；macro 先给每类各算 F1
          再平均，小类与大类权重相同。二者对照可以发现总分掩盖的小类问题。
        </p>
        <div v-if="!evaluation.present" class="miss">{{ evaluation.hint }}</div>
        <template v-else
          ><div class="avg">
            <div v-for="key in ['micro', 'macro'] as const" :key="key" class="avgbox">
              <h3>{{ key }}</h3>
              <div class="way">
                {{ key === 'micro' ? '不分科：所有对错混一个桶' : '分科：17 类各算再平均' }}
              </div>
              <div class="nums">
                <div v-for="metric in ['p', 'r', 'f1'] as const" :key="metric">
                  <b>{{ score(evaluation[key]?.[metric]) }}</b
                  ><span>{{ metric.toUpperCase() }}</span>
                </div>
              </div>
            </div>
          </div>
          <div v-if="gap != null" class="tip">
            两者相差 {{ score(gap) }}：{{
              gap <= 0.02
                ? '各类成绩均匀，没有类目出问题被总分盖住。'
                : '差距偏大，请结合每类指标检查是否有小类目被牺牲。'
            }}
          </div></template
        ><JobRow :jobs="jobsFor(['ch10-eval'])" @finish="refresh" />
      </section>
      <section class="panel">
        <h2>
          每类指标与容错红线
          <span
            v-if="evaluation.present"
            class="pill"
            :class="evaluation.red_line_passed ? 'pass' : 'fail'"
            >{{ evaluation.red_line_passed ? '红线全过' : '有类目跌破红线' }}</span
          >
        </h2>
        <p class="lede">
          严档 F1 ≥ 0.9，中档 ≥ 0.8，宽档不设红线，显示 —。support 是测试集中该类题数，等于 TP +
          FN。
        </p>
        <div v-if="!evaluation.present" class="miss">{{ evaluation.hint }}</div>
        <div v-else class="scroll-x">
          <table class="tbl">
            <thead>
              <tr>
                <th>类目</th>
                <th>档位</th>
                <th>P</th>
                <th>R</th>
                <th>F1</th>
                <th>support</th>
                <th>红线</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="item in evaluation.classes"
                :key="item.name"
                :class="{ bad: item.passed === false }"
              >
                <td>{{ item.name }}</td>
                <td>
                  <span class="pill" :class="'sev-' + item.severity">{{ item.severity }}</span>
                </td>
                <td><ScoreCell :value="item.p" /></td>
                <td><ScoreCell :value="item.r" /></td>
                <td><ScoreCell :value="item.f1" :threshold="item.red_line" /></td>
                <td class="num">{{ item.support }}</td>
                <td>
                  <span v-if="item.red_line == null">—</span
                  ><span v-else class="pill" :class="item.passed ? 'pass' : 'fail'"
                    >{{ item.red_line }} {{ item.passed ? '✅' : '🔴 回头搞数据' }}</span
                  >
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <!-- 扫描条按同一报告的 min~max 拉伸，以保留旧页面的第三位小数可读性。 -->
      <section class="panel">
        <h2>
          判定阈值：九候选线扫描重演
          <span v-if="scan.present" class="pill" :class="scan.consistent ? 'pass' : 'fail'">{{
            scan.consistent ? '与在用阈值一致' : '与在用阈值不一致'
          }}</span>
        </h2>
        <p class="lede">
          固定验证集分数表，扫描 0.30~0.70、步进 0.05 的九条候选线，以 micro-F1
          选优，打平时保留先到的阈值。
        </p>
        <div v-if="!scan.present" class="miss">{{ scan.hint }}</div>
        <template v-else
          ><div class="scan">
            <div
              v-for="row in scan.scan"
              :key="row.threshold"
              class="scanrow"
              :class="{
                win: close(row.threshold, scan.best_threshold),
                inuse: close(row.threshold, scan.in_use_threshold),
              }"
            >
              <span class="t">{{ score(row.threshold, 2) }}</span
              ><span class="track"
                ><span class="fill" :style="{ width: scanWidth(row.micro_f1) }" /></span
              ><span class="f1">{{ score(row.micro_f1, 4) }}</span
              ><span class="tag"
                >{{ close(row.threshold, scan.best_threshold) ? '当选' : ''
                }}{{
                  close(row.threshold, scan.in_use_threshold) ? ' · threshold.json 在用' : ''
                }}</span
              >
            </div>
          </div>
          <div class="tip">
            重演当选 {{ score(scan.best_threshold, 2) }}（验证集 micro-F1
            {{ score(scan.best_micro_f1, 4) }}），threshold.json 在用
            {{ scan.in_use_threshold ?? '—' }}，{{
              scan.consistent ? '两者一致 ✅' : '两者不一致 🔴，重新导出或重跑扫描'
            }}。验证集 {{ scan.val_size }} 条，跑于 {{ time(scan.ran_at) }}。
          </div></template
        ><JobRow :jobs="jobsFor(['ch10-threshold-scan'])" @finish="refresh" />
      </section>
      <section class="panel">
        <h2>每类混淆矩阵：错题本</h2>
        <p class="lede">
          TP 该打打对了，TN 不该打也没打，FP 冤枉，FN 放跑。除了数量，还要关注错误方向。
        </p>
        <div v-if="!evaluation.present" class="miss">{{ evaluation.hint }}</div>
        <template v-else
          ><div class="tip">
            全表 {{ evaluation.total_cells }} 道是非题（{{ evaluation.test_size }} 条 × 17 类）错
            {{ (evaluation.total_fp ?? 0) + (evaluation.total_fn ?? 0) }} 道：冤枉
            {{ evaluation.total_fp }} 次、放跑 {{ evaluation.total_fn }} 次。
          </div>
          <div class="mats">
            <article
              v-for="item in evaluation.classes"
              :key="item.name"
              class="mat"
              :class="{ dirty: item.fp || item.fn }"
            >
              <div class="cap">
                <span>{{ item.name }}</span
                ><span class="spacer" /><span class="pill" :class="'sev-' + item.severity">{{
                  item.severity
                }}</span>
              </div>
              <div class="matrix">
                <div v-for="cell in matrixCells" :key="cell.key" class="c" :class="cell.key">
                  <b>{{ item[cell.key] }}</b
                  ><span>{{ cell.label }}</span>
                </div>
              </div>
            </article>
          </div>
          <RouterLink class="btn sm errors-link" to="/acceptance/errors"
            >去错例复核页看这些错具体错在哪 →</RouterLink
          ></template
        >
      </section>
      <ClassifierTry :online="data.classifier.online" />
    </template>
  </AdminShell>
</template>
<style scoped>
.avg {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.avgbox {
  border: 3px solid var(--ink);
  background: var(--paper);
  padding: 10px 14px;
}
.avgbox h3 {
  margin: 0;
  font-size: 16px;
}
.way {
  font-size: 11.5px;
  color: var(--muted);
  margin-top: 3px;
}
.nums {
  display: flex;
  gap: 26px;
  margin-top: 10px;
}
.nums b {
  display: block;
  font-size: 23px;
}
.nums span {
  font-size: 11px;
  color: var(--muted);
}
.scan {
  margin-top: 12px;
}
.scanrow {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 6px 8px;
  font-size: 12px;
}
.scanrow.win {
  background: #fff0d6;
}
.scanrow.inuse {
  outline: 2px solid var(--ink);
}
.t {
  width: 38px;
  font-weight: 700;
}
.track {
  display: block;
  flex: 1;
  height: 14px;
  border: 2px solid var(--ink);
  background: var(--paper);
}
.fill {
  display: block;
  height: 100%;
  background: var(--fur);
}
.win .fill {
  background: var(--online);
}
.f1 {
  font-variant-numeric: tabular-nums;
}
.tag {
  width: 180px;
  font-size: 11px;
}
.mats {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 12px;
  margin-top: 12px;
}
.mat {
  border: 3px solid var(--ink);
  background: var(--paper);
}
.mat.dirty {
  border-color: var(--error);
}
.cap {
  display: flex;
  gap: 6px;
  align-items: center;
  padding: 6px 8px;
  font-size: 12px;
  font-weight: 700;
  border-bottom: 2px solid var(--ink);
}
.matrix {
  display: grid;
  grid-template-columns: 1fr 1fr;
}
.c {
  padding: 7px 9px;
  border: 1px solid var(--ink);
}
.c b {
  font-size: 20px;
  display: block;
}
.c span {
  font-size: 10px;
  color: var(--muted);
}
.tp {
  background: #e8fae9;
}
.dirty .fp,
.dirty .fn {
  background: #ffe9ec;
}
.errors-link {
  margin-top: 12px;
}
.date {
  font-size: 14px;
  line-height: 2;
}
@media (max-width: 700px) {
  .avg {
    grid-template-columns: 1fr;
  }
  .tag {
    width: 100px;
  }
  .scanrow {
    gap: 5px;
  }
}
</style>
