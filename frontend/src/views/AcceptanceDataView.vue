<!-- 分类器数据血缘：语料流转、切分泄漏、训练三件套及 ONNX 一致性证据。 -->
<script setup lang="ts">
import { computed, onMounted } from 'vue'
import AdminShell from '../components/admin/AdminShell.vue'
import FileTable from '../components/admin/FileTable.vue'
import JobRow from '../components/admin/JobRow.vue'
import { useResource } from '../composables/useResource'
import type { AcceptanceData, SplitKey } from '../types/acceptance'
import type { JobSpec } from '../types/jobs'
import { bytes, time } from '../utils/format'

const report = useResource<AcceptanceData>()
const jobs = useResource<{ jobs: JobSpec[] }>()
const { data, loading, error } = report
const splits: { key: SplitKey; label: string }[] = [
  { key: 'train', label: '训练' },
  { key: 'val', label: '验证' },
  { key: 'test', label: '测试' },
]
const leaks = [
  { key: 'train_val' as const, label: '训练 ∩ 验证' },
  { key: 'train_test' as const, label: '训练 ∩ 测试' },
  { key: 'val_test' as const, label: '验证 ∩ 测试' },
]
const corpus = computed(() =>
  data.value?.lineage.find((item) => item.file === 'corpus_labeled.jsonl'),
)
const jobsFor = (names: string[]) =>
  names.flatMap((name) => jobs.data.value?.jobs.filter((job) => job.name === name) ?? [])
const distributionWidth = (key: SplitKey, name: string) => {
  const counts = data.value?.dataset.splits[key].counts ?? {}
  return `${Math.round(((counts[name] ?? 0) / Math.max(1, ...Object.values(counts))) * 100)}%`
}
const refresh = () => Promise.all([report.load('/api/acceptance/data'), jobs.load('/api/jobs')])
onMounted(refresh)
</script>
<template>
  <AdminShell
    title="分类器数据血缘"
    subtitle="语料从哪来 · 考卷有没有泄漏 · 产物是否齐全"
    :loading="loading"
    :error="error || jobs.error.value"
    @refresh="refresh"
  >
    <template v-if="data">
      <div class="gatebar">
        <div class="stat">
          <span>语料总量</span><b>{{ corpus?.present ? corpus.lines : '—' }} 条</b>
        </div>
        <div v-for="split in splits" :key="split.key" class="stat">
          <span>{{ split.label }}</span
          ><b>{{ data.dataset.splits[split.key].size }} 条</b>
        </div>
        <div class="stat" :class="data.dataset.clean ? 'pass' : 'fail'">
          <span>考卷泄漏</span><b>{{ data.dataset.clean ? '0 条' : '有' }}</b>
        </div>
        <div class="stat">
          <span>在用阈值</span><b>{{ data.model.threshold ?? '—' }}</b>
        </div>
      </div>
      <!-- 语料清单与标准化问法血缘。 -->
      <section class="panel">
        <h2>语料血缘：一条问题从池子走到考卷</h2>
        <p class="lede">
          每段对应一个产物文件。捞池使用飞轮归并后的标准化问法，分类器接手的是语义完整的问题。
        </p>
        <div class="flow">
          <template v-for="(step, index) in data.lineage" :key="step.path"
            ><span v-if="index" class="arrow">→</span>
            <div class="step" :class="{ absent: !step.present }">
              <div class="stage">{{ step.stage }}</div>
              <b>{{ step.present ? (step.lines ?? '—') : '缺失' }}</b>
              <div class="desc">{{ step.desc }}</div>
              <div class="f">{{ step.file }}</div>
            </div></template
          ><span class="arrow">→</span>
          <div class="step exam">
            <div class="stage">分层切卷 80/10/10</div>
            <b>{{ splits.map((split) => data!.dataset.splits[split.key].size).join(' / ') }}</b>
            <div class="desc">训练 / 验证 / 测试；训练集另做增强与定向补数</div>
            <div class="f">data/ch10/dataset/*.jsonl</div>
          </div>
        </div>
        <FileTable :files="[...data.lineage, data.sample_review]" />
        <div class="tip">
          人工抽审文件 sample_review.md 包含真实池全量及每类模拟抽 5
          条。看到错标直接修正语料，不改考卷凑分。
        </div>
        <JobRow :jobs="jobsFor(['ch10-corpus', 'ch10-dataset'])" @finish="refresh" />
      </section>
      <!-- 切分和泄漏证据直接来自后端，条数与标签命中数采用不同口径。 -->
      <section class="panel">
        <h2>
          三份考卷与泄漏自检
          <span class="pill" :class="data.dataset.clean ? 'pass' : 'fail'">{{
            data.dataset.clean ? '零重叠' : '有重叠，分数不作数'
          }}</span>
        </h2>
        <p class="lede">
          增强只扩训练集。训练、验证、测试的重叠必须为 0，否则成绩不能证明泛化能力。
        </p>
        <div class="leak">
          <div
            v-for="leak in leaks"
            :key="leak.key"
            class="stat"
            :class="data.dataset.leaks[leak.key] === 0 ? 'pass' : 'fail'"
          >
            <span>{{ leak.label }}</span
            ><b>{{ data.dataset.leaks[leak.key] }} 条</b>
          </div>
          <div v-for="split in splits" :key="split.key" class="stat">
            <span>{{ split.label }} 多标签</span
            ><b>{{ data.dataset.splits[split.key].multi_label }} 条</b>
          </div>
        </div>
        <div class="scroll-x distribution">
          <table class="tbl">
            <thead>
              <tr>
                <th>类目</th>
                <th v-for="split in splits" :key="split.key">
                  {{ split.label }}（{{ data.dataset.splits[split.key].size }}）
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="name in data.topic_names" :key="name">
                <td>{{ name }}</td>
                <td v-for="split in splits" :key="split.key">
                  <div class="distbar">
                    <span class="track"
                      ><span
                        class="fill"
                        :style="{ width: distributionWidth(split.key, name) }" /></span
                    ><span>{{ data.dataset.splits[split.key].counts[name] ?? 0 }}</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="tip">
          标签计数按「命中即计」计算，多标签句给每个命中类各记一次，所以各列合计可能大于条数。
        </div>
      </section>
      <section class="panel">
        <h2>
          训练产物三件套
          <span class="pill" :class="data.model.trio_ok ? 'pass' : 'fail'">{{
            data.model.trio_ok ? '三件套齐全' : '三件套不全'
          }}</span>
        </h2>
        <p class="lede">权重 + tokenizer + threshold.json。评测和服务读取同一份阈值文件。</p>
        <FileTable :files="data.model.files" />
        <div class="tip">
          <b>threshold.json</b> 在用判定阈值 {{ data.model.threshold ?? '—' }}；写入于
          {{ time(data.model.threshold_file.mtime) }}，{{
            bytes(data.model.threshold_file.bytes)
          }}。
        </div>
        <JobRow :jobs="jobsFor(['ch10-train'])" @finish="refresh" />
      </section>
      <section class="panel">
        <h2>
          ONNX 导出产物与一致性校验
          <span
            class="pill"
            :class="
              !data.onnx.report.present ? 'missing' : data.onnx.report.passed ? 'pass' : 'fail'
            "
            >{{
              !data.onnx.report.present
                ? '未导出'
                : data.onnx.report.passed
                  ? '预测完全一致'
                  : '有不一致'
            }}</span
          >
        </h2>
        <p class="lede">
          导出后用测试集逐条对齐 torch，过线标签完全一致才放行；服务只加载 onnxruntime +
          tokenizers。
        </p>
        <FileTable :files="data.onnx.files" />
        <div v-if="data.onnx.report.present" class="tip">
          校验 {{ data.onnx.report.checked }} 条，不一致 {{ data.onnx.report.mismatch }} 条；opset
          {{ data.onnx.report.opset }}，model.onnx {{ bytes(data.onnx.report.onnx_bytes) }}，导出于
          {{ time(data.onnx.report.ran_at) }}。
        </div>
        <div v-else class="miss">{{ data.onnx.report.hint || '导出产物尚未生成' }}</div>
        <JobRow
          :jobs="jobsFor(['ch10-export', 'classifier-up', 'classifier-down'])"
          @finish="refresh"
        />
      </section>
    </template>
  </AdminShell>
</template>
<style scoped>
.flow {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin: 12px 0;
}
.step {
  border: 3px solid var(--ink);
  background: var(--paper);
  padding: 9px 12px;
  flex: 1;
  min-width: 150px;
}
.step.absent {
  border-style: dashed;
  color: var(--muted);
}
.step.exam {
  background: var(--fur);
}
.stage {
  font-size: 12px;
  font-weight: 700;
}
.step b {
  display: block;
  font-size: 22px;
  margin: 5px 0;
}
.desc,
.f {
  font-size: 11.5px;
  line-height: 1.7;
  color: #6d6478;
}
.f {
  overflow-wrap: anywhere;
}
.arrow {
  font-size: 22px;
}
.leak {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.distribution {
  margin-top: 12px;
}
.distbar {
  display: flex;
  align-items: center;
  gap: 7px;
  justify-content: flex-end;
  font-variant-numeric: tabular-nums;
}
.track {
  display: block;
  width: 90px;
  height: 9px;
  border: 2px solid var(--ink);
  background: var(--cream);
}
.fill {
  display: block;
  height: 100%;
  background: var(--fur);
}
</style>
