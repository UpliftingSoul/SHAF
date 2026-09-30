<!-- RAG 生成评估：答案覆盖度、分桶忠实度、拒答圆环和该拒没拒个案。 -->
<script setup lang="ts">
import RagChart from './RagChart.vue'
import ReadNote from './ReadNote.vue'
import { buckets, metricValue, type RagReport } from '../../types/ragEval'
import { percent, score } from '../../utils/format'
defineProps<{ report: RagReport }>()
const circumference = 2 * Math.PI * 52
</script>
<template>
  <section class="panel">
    <h2>
      生成质量(端到端)
      <span class="pill" :class="report.generation_done ? 'info' : 'missing'">{{
        report.generation_done ? 'LLM 裁判判分' : '本次未完成'
      }}</span>
    </h2>
    <p class="lede">
      四策略将证据交给同一模型生成答案，由裁判评估标准要点覆盖度。同时查看上线管线忠实度与库外拒答。
    </p>
    <div v-if="!report.generation_done || !report.generation" class="miss">
      生成段没跑完，裁判模型上游不可用。这一段的数缺着；上游恢复后重跑评估补齐，检索段不受影响。
    </div>
    <template v-else
      ><div class="subhead">
        四策略答案覆盖度 <span>生成答案盖住标准要点的比例 · LLM 判分</span>
      </div>
      <RagChart :report="report" metric="答案覆盖度" /><ReadNote
        :note="report.read_notes?.rag_answer_coverage"
        :fallback="`同一套生成提示词，只换检索策略：混合 + 重排总体答案覆盖度 ${score(metricValue(report, '答案覆盖度', 'hybrid_rerank', 'overall'), 2)}，纯 BM25 ${score(metricValue(report, '答案覆盖度', 'bm25', 'overall'), 2)}。结合检索证据判断遗漏原因。`"
      />
      <div class="gen-sub">
        <div>
          <div class="subhead">忠实度 Faithfulness <span>混合 + 重排 · 答案有没有编造</span></div>
          <div class="faith-rows">
            <div v-for="bucket in buckets.filter((b) => b.key !== 'overall')" :key="bucket.key">
              <div class="fr-label">
                <span
                  ><b>{{ bucket.label }}</b
                  ><small
                    >{{ bucket.key }} · 评
                    {{ report.generation.faithfulness?.[bucket.key]?.answered ?? 0 }} 题</small
                  ></span
                ><b>{{ score(report.generation.faithfulness?.[bucket.key]?.v, 2) }}</b>
              </div>
              <div class="bar-track">
                <span
                  class="bar-fill"
                  :class="{
                    mid: (report.generation.faithfulness?.[bucket.key]?.v ?? 0) < 0.9,
                    low: (report.generation.faithfulness?.[bucket.key]?.v ?? 0) < 0.7,
                  }"
                  :style="{
                    width: `${Math.max(2, (report.generation.faithfulness?.[bucket.key]?.v ?? 0) * 100)}%`,
                    opacity: report.generation.faithfulness?.[bucket.key]?.v == null ? 0.25 : 1,
                  }"
                />
              </div>
            </div>
          </div>
        </div>
        <div class="gauge">
          <div class="ring">
            <svg width="118" height="118" viewBox="0 0 118 118">
              <circle cx="59" cy="59" r="52" fill="none" stroke="#fff6e6" stroke-width="13" />
              <circle
                v-if="report.generation.refusal.rate != null"
                cx="59"
                cy="59"
                r="52"
                fill="none"
                stroke="var(--online)"
                stroke-width="13"
                :stroke-dasharray="circumference"
                :stroke-dashoffset="circumference * (1 - report.generation.refusal.rate)"
              />
            </svg>
            <div class="cval">
              <span>{{ percent(report.generation.refusal.rate, 0) }}</span
              ><small>拒答率</small>
            </div>
          </div>
          <div class="glab">
            <b>D_absent 桶</b> · 库外问题<br />{{ report.generation.refusal.correct }} /
            {{ report.generation.refusal.total }} 正确拒答并落池
          </div>
        </div>
      </div>
      <details v-if="report.generation.refusal.cases?.length" class="faith">
        <summary>
          该拒没拒的个案
          <span class="pill sev-中">{{ report.generation.refusal.cases.length }} 例</span> ·
          点开看是哪一条 / 被什么证据骗过了闸
        </summary>
        <div class="fcbody">
          <p class="lead">
            这些库外问题被两道证据闸放行。检查被误当成答案的证据，以收紧判据或补充明确的不支持知识。
          </p>
          <article v-for="item in report.generation.refusal.cases" :key="item.id" class="fcase">
            <div class="fh">
              <span class="fid">{{ item.id }}</span
              ><b>{{ item.query }}</b
              ><small>库外桶</small>
            </div>
            <div class="frow">
              <span class="k">被当成答案的小节</span>{{ item.section_path || '—' }}
            </div>
            <div class="frow">
              <span class="k">那条证据(原文)</span>
              <div class="ans">{{ item.evidence }}</div>
            </div>
          </article>
        </div>
      </details>
      <div v-else class="faith-none">
        {{
          report.generation.refusal.total
            ? `✓ 库外桶评上的 ${report.generation.refusal.total} 道题全部正确拒答，没有「该拒没拒」的个案。`
            : '本轮库外题没有可用评估结果。'
        }}
      </div>
      <div v-if="report.generation.skipped" class="tip">
        本轮有 {{ report.generation.skipped }} 次裁判调用超时或失败被跳过，按可用样本计。{{
          report.generation.refusal.skipped_ids?.length
            ? '库外桶未评上的题：' + report.generation.refusal.skipped_ids.join('、')
            : ''
        }}
      </div>
    </template>
  </section>
</template>
<style scoped>
.subhead {
  font-size: 13px;
  font-weight: 700;
}
.subhead span {
  color: var(--muted);
  font-weight: 400;
  font-size: 11.5px;
  margin-left: 7px;
}
.gen-sub {
  display: grid;
  grid-template-columns: 1.45fr 1fr;
  gap: 14px;
  margin-top: 14px;
}
.faith-rows {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 8px;
}
.fr-label {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 12.5px;
}
.fr-label small {
  font-size: 11px;
  color: var(--muted);
  margin-left: 6px;
}
.bar-track {
  height: 14px;
  border: 2px solid var(--ink);
  background: var(--cream);
  margin-top: 4px;
}
.bar-fill {
  height: 100%;
  background: var(--online);
  display: block;
}
.bar-fill.mid {
  background: var(--fur);
}
.bar-fill.low {
  background: var(--error);
}
.gauge {
  border: 3px solid var(--ink);
  background: var(--paper);
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 14px 12px;
}
.ring {
  position: relative;
  width: 118px;
  height: 118px;
}
.ring svg {
  transform: rotate(-90deg);
}
.cval {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.cval span {
  font-size: 25px;
  font-weight: 700;
}
.cval small {
  font-size: 10.5px;
  color: var(--muted);
}
.glab {
  margin-top: 10px;
  font-size: 12px;
  line-height: 1.7;
  color: #6d6478;
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
@media (max-width: 820px) {
  .gen-sub {
    grid-template-columns: 1fr;
  }
}
</style>
