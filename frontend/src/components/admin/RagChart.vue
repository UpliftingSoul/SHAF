<!-- 四策略分组柱图：统一桶、色彩与 0~1 坐标；缺值以淡条和 — 表示。 -->
<script setup lang="ts">
import { computed } from 'vue'
import {
  buckets,
  strategies,
  metricValue,
  type RagMetric,
  type RagReport,
} from '../../types/ragEval'
import { score } from '../../utils/format'
const props = defineProps<{ report: RagReport; metric: RagMetric }>()
const groups = computed(() =>
  buckets.map((bucket, index) => ({
    ...bucket,
    x: 40 + index * 138.4,
    bars: strategies.map((strategy, s) => {
      const value = metricValue(props.report, props.metric, strategy.key, bucket.key)
      const height = Math.max(2, Math.max(0, Math.min(1, value ?? 0)) * 270)
      return { ...strategy, value, x: 49 + index * 138.4 + s * 22.6, y: 290 - height, height }
    }),
  })),
)
</script>
<template>
  <div class="legend">
    <span
      v-for="strategy in strategies"
      :key="strategy.key"
      :class="{ win: strategy.key === report.best?.strategy }"
      ><i :style="{ background: strategy.color }" />{{ strategy.label
      }}{{ strategy.key === report.best?.strategy ? '(最佳)' : '' }}</span
    >
  </div>
  <div class="chart-scroll">
    <svg class="chart" viewBox="0 0 720 348" role="img" :aria-label="metric + '四策略分桶对照图'">
      <g v-for="tick in [0, 0.25, 0.5, 0.75, 1]" :key="tick">
        <line x1="40" x2="706" :y1="290 - tick * 270" :y2="290 - tick * 270" class="grid" />
        <text x="32" :y="294 - tick * 270" text-anchor="end" class="axis">
          {{ tick.toFixed(2) }}
        </text>
      </g>
      <line x1="40" x2="706" y1="290" y2="290" class="base" />
      <g v-for="group in groups" :key="group.key">
        <line
          v-if="group.key === 'overall'"
          :x1="group.x - 13"
          :x2="group.x - 13"
          y1="18"
          y2="290"
          class="divider"
        />
        <g v-for="bar in group.bars" :key="bar.key">
          <rect
            :x="bar.x"
            :y="bar.y"
            width="18.6"
            :height="bar.height"
            :fill="bar.color"
            :opacity="bar.value == null ? 0.25 : 1"
            class="bar"
          >
            <title>{{ group.label }} · {{ bar.label }}：{{ score(bar.value, 2) }}</title>
          </rect>
          <text :x="bar.x + 9.3" :y="bar.y - 5" text-anchor="middle" class="vlabel">
            {{ score(bar.value, 2) }}
          </text>
        </g>
        <text :x="group.x + 56" y="311" text-anchor="middle" class="xlabel">{{ group.label }}</text>
        <text :x="group.x + 56" y="329" text-anchor="middle" class="axis">{{ group.key }}</text>
      </g>
    </svg>
  </div>
</template>
<style scoped>
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  font-size: 12px;
  margin: 12px 0;
}
.legend span {
  display: flex;
  gap: 6px;
  align-items: center;
}
.legend i {
  width: 12px;
  height: 12px;
  border: 2px solid var(--ink);
}
.legend .win {
  font-weight: 700;
}
.chart-scroll {
  overflow-x: auto;
}
.chart {
  display: block;
  width: 100%;
  min-width: 650px;
}
.grid {
  stroke: #ddd2c3;
  stroke-width: 1;
}
.base,
.bar {
  stroke: var(--ink);
  stroke-width: 2;
}
.divider {
  stroke: var(--muted);
  stroke-dasharray: 4 4;
}
.axis {
  font-size: 10px;
  fill: var(--muted);
}
.xlabel {
  font-size: 12px;
  font-weight: 700;
  fill: var(--ink);
}
.vlabel {
  font-size: 10px;
  fill: var(--ink);
}
</style>
