<!-- 分类器错例复核：区分样本条数和矩阵笔数，展示边界配对与逐条标签差异。 -->
<script setup lang="ts">
import { computed, onMounted } from 'vue'
import AdminShell from '../components/admin/AdminShell.vue'
import JobRow from '../components/admin/JobRow.vue'
import { useResource } from '../composables/useResource'
import type { ErrorReport } from '../types/acceptance'
import type { JobSpec } from '../types/jobs'
import { time } from '../utils/format'

const report = useResource<ErrorReport>()
const jobs = useResource<{ jobs: JobSpec[] }>()
const { data, loading, error } = report
const evalJobs = computed(
  () => jobs.data.value?.jobs.filter((job) => job.name === 'ch10-eval') ?? [],
)
const kindClass: Record<string, string> = { 漏打: 'info', 错位: 'fail', 多打: 'running' }
const refresh = () => Promise.all([report.load('/api/acceptance/errors'), jobs.load('/api/jobs')])
onMounted(refresh)
</script>
<template>
  <AdminShell
    title="分类器错例复核"
    subtitle="错在哪一类 · 是冤枉还是放跑 · 标注本身有没有毛病"
    :loading="loading"
    :error="error || jobs.error.value"
    @refresh="refresh"
  >
    <template v-if="data">
      <div v-if="!data.eval.present" class="gatebar">
        <div class="stat fail"><span>评测产物</span><b>未生成</b></div>
      </div>
      <div v-else class="gatebar">
        <div class="stat">
          <span>错例</span><b>{{ data.errors.length }} 条</b>
        </div>
        <div class="stat">
          <span>矩阵笔数</span><b>{{ data.matrix_entries }} 笔</b>
        </div>
        <div class="stat">
          <span>冤枉 FP</span><b>{{ data.total_fp }} 次</b>
        </div>
        <div class="stat">
          <span>放跑 FN</span><b>{{ data.total_fn }} 次</b>
        </div>
        <div class="stat">
          <span>测试集</span><b>{{ data.eval.test_size }} 条</b>
        </div>
        <div class="stat">
          <span>评测于</span><b class="date">{{ time(data.eval.ran_at).slice(5, 16) }}</b>
        </div>
      </div>
      <section v-if="!data.eval.present" class="panel">
        <h2>错例复核</h2>
        <div class="miss">{{ data.eval.hint || '评测产物还没生成，先跑 make ch10-eval' }}</div>
        <JobRow :jobs="evalJobs" @finish="refresh" />
      </section>
      <template v-else>
        <section class="panel">
          <h2>这笔账怎么算：错例条数 ≠ 矩阵笔数</h2>
          <p class="lede">
            漏打记 1 次放跑；多打记 1 笔冤枉；错位同时记被抢类放跑和抢标签类冤枉，共 2
            笔。因此清单条数与矩阵笔数有不同口径。
          </p>
          <div class="kinds">
            <div v-for="(count, kind) in data.kinds" :key="kind" class="kindbox">
              <span class="pill" :class="kindClass[kind] || 'info'">{{ kind }}</span
              ><span class="n"> {{ count }} 条</span>
              <div class="rp">修法：{{ data.recipes?.[kind] || '—' }}</div>
            </div>
          </div>
          <div class="tip">
            {{ data.errors.length }} 条错例 → 矩阵记 {{ data.matrix_entries }} 笔：冤枉
            {{ data.total_fp }} 次、放跑
            {{ data.total_fn }} 次。训练有成本，应结合错例积累情况决定是否重训。
          </div>
        </section>
        <section class="panel">
          <h2>边界摩擦：哪两类在抢标签</h2>
          <p class="lede">
            把「该打没打的类 ←
            反而打了的类」按对计数。反复出现的配对应成对补对照句，让模型学会区分语境。
          </p>
          <div v-if="!data.pairs.length" class="miss">本轮没有错位型错误，没有类目在互抢标签</div>
          <template v-else
            ><div class="scroll-x">
              <table class="tbl">
                <thead>
                  <tr>
                    <th>被抢的类（该打没打）</th>
                    <th>档位</th>
                    <th>抢标签的类（冤枉打上）</th>
                    <th>次数</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="pair in data.pairs"
                    :key="pair.missed + ':' + pair.grabbed"
                    :class="{ bad: pair.count > 1 }"
                  >
                    <td>
                      <span class="lb missed">{{ pair.missed }}</span>
                    </td>
                    <td>
                      <span v-if="pair.severity" class="pill" :class="'sev-' + pair.severity">{{
                        pair.severity
                      }}</span>
                    </td>
                    <td>
                      <span class="lb extra">{{ pair.grabbed }}</span>
                    </td>
                    <td class="num">×{{ pair.count }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="tip">出现 2 次以上的配对已标红，优先补它的对照句。</div></template
          >
        </section>
        <section class="panel">
          <h2>逐条对照：标准答案 vs 模型预测</h2>
          <p class="lede">
            标准答案里被放跑的类标红，模型多打上的类标橙，两边都对上的标绿。错别字和方言模拟真实输入，噪声下的错例照样计入。
          </p>
          <article v-for="(item, index) in data.errors" :key="index" class="ecard">
            <div class="cmp">
              <span class="pill" :class="kindClass[item.kind] || 'info'">{{ item.kind }}</span
              ><span class="k">记 {{ item.matrix_entries }} 笔矩阵账</span>
            </div>
            <div class="q">「{{ item.text }}」</div>
            <div class="cmp">
              <div class="side">
                <span class="k">标准</span><span v-if="!item.gold.length" class="lb">（无）</span
                ><span
                  v-for="label in item.gold"
                  :key="label"
                  class="lb"
                  :class="item.missed.includes(label) ? 'missed' : 'ok'"
                  >{{ label }}</span
                >
              </div>
              <div class="side">
                <span class="k">预测</span><span v-if="!item.pred.length" class="lb">（无）</span
                ><span
                  v-for="label in item.pred"
                  :key="label"
                  class="lb"
                  :class="item.extra.includes(label) ? 'extra' : 'ok'"
                  >{{ label }}</span
                >
              </div>
            </div>
            <div class="why">
              <template v-if="item.missed.length">放跑：{{ item.missed.join('、') }}　|　</template
              ><template v-if="item.extra.length">冤枉：{{ item.extra.join('、') }}　|　</template
              >修法：{{ data.recipes?.[item.kind] || '—' }}
            </div>
          </article>
          <JobRow :jobs="evalJobs" @finish="refresh" />
        </section>
      </template>
    </template>
  </AdminShell>
</template>
<style scoped>
.ecard {
  border: 3px solid var(--ink);
  background: var(--paper);
  padding: 10px 13px;
  margin-top: 10px;
}
.q {
  font-size: 13.5px;
  font-weight: 700;
  line-height: 1.7;
}
.cmp {
  margin-top: 8px;
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  font-size: 12.5px;
}
.side {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.k {
  color: var(--muted);
}
.lb {
  border: 2px solid var(--ink);
  padding: 1px 7px;
  font-size: 12px;
  background: var(--cream);
  white-space: nowrap;
}
.lb.ok {
  background: var(--online);
}
.lb.missed {
  background: #ffd4dc;
  border-color: var(--error);
  color: #8e1f31;
}
.lb.extra {
  background: var(--fur);
}
.why {
  margin-top: 8px;
  font-size: 12px;
  color: #6d6478;
  line-height: 1.75;
}
.kinds {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.kindbox {
  border: 3px solid var(--ink);
  background: var(--paper);
  padding: 9px 13px;
  min-width: 200px;
  flex: 1;
}
.n {
  font-size: 20px;
  font-weight: 700;
}
.rp {
  font-size: 11.5px;
  color: #6d6478;
  line-height: 1.7;
  margin-top: 4px;
}
.date {
  font-size: 14px;
  line-height: 2;
}
</style>
