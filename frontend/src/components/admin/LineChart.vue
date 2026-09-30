<!-- 响应式 SVG 折线图：只把报告中的点映射到坐标，标记推荐线和在用线。 -->
<script setup lang="ts">
const props = defineProps<{
  series: { label: string; color: string; points: { x: number; y: number | null }[] }[]
  minX: number
  maxX: number
  ticks: number[]
  xLabel: string
  markers?: { x: number; label: string; color: string; dashed?: boolean }[]
}>()
const sx = (value: number) => 42 + ((value - props.minX) / (props.maxX - props.minX || 1)) * 664
const sy = (value: number) => 254 - value * 238
function path(points: { x: number; y: number | null }[]) {
  let connected = false
  return points
    .map((point) => {
      if (point.y == null) {
        connected = false
        return ''
      }
      const segment = `${connected ? 'L' : 'M'}${sx(point.x)},${sy(point.y)}`
      connected = true
      return segment
    })
    .join(' ')
}
</script>
<template>
  <div class="chart-scroll">
    <div class="legend">
      <span v-for="line in series" :key="line.label"
        ><i :style="{ background: line.color }" />{{ line.label }}</span
      ><span v-for="marker in markers" :key="marker.label"
        ><i :style="{ background: marker.color }" />{{ marker.label }}</span
      >
    </div>
    <svg class="chart" viewBox="0 0 720 300" role="img" :aria-label="`${xLabel}扫描曲线`">
      <g v-for="tick in [0, 0.25, 0.5, 0.75, 1]" :key="tick">
        <line x1="42" x2="706" :y1="sy(tick)" :y2="sy(tick)" class="grid" />
        <text x="34" :y="sy(tick) + 3.5" text-anchor="end" class="axis">{{ tick.toFixed(2) }}</text>
      </g>
      <line x1="42" x2="706" y1="254" y2="254" class="base" />
      <text
        v-for="tick in ticks"
        :key="tick"
        :x="sx(tick)"
        y="274"
        text-anchor="middle"
        class="axis"
      >
        {{ tick.toFixed(2) }}
      </text>
      <text x="374" y="292" text-anchor="middle" class="glabel">{{ xLabel }}</text>
      <path
        v-for="line in series"
        :key="line.label"
        :d="path(line.points)"
        fill="none"
        :stroke="line.color"
        stroke-width="3"
      />
      <g v-for="marker in markers" :key="marker.label">
        <line
          :x1="sx(marker.x)"
          :x2="sx(marker.x)"
          y1="16"
          y2="254"
          :stroke="marker.color"
          :stroke-dasharray="marker.dashed ? '5 4' : undefined"
          stroke-width="2"
        />
        <text :x="sx(marker.x) + 6" y="28" class="glabel">
          {{ marker.label }} {{ marker.x.toFixed(2) }}
        </text>
      </g>
    </svg>
  </div>
</template>
<style scoped>
.chart-scroll {
  overflow-x: auto;
}
.chart {
  display: block;
  width: 100%;
  min-width: 520px;
}
.grid {
  stroke: var(--muted);
  stroke-opacity: 0.3;
}
.base {
  stroke: var(--ink);
  stroke-width: 2;
}
.axis,
.glabel {
  fill: var(--ink);
  font-size: 11px;
}
.legend {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 11.5px;
  margin: 8px 0;
}
.legend span {
  display: flex;
  align-items: center;
  gap: 5px;
}
.legend i {
  display: inline-block;
  width: 12px;
  height: 10px;
  border: 1px solid var(--ink);
}
</style>
