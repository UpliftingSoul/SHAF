<!-- 指标单元格：数值加横条，仅在有明确红线时显示达标颜色。 -->
<script setup lang="ts">
import { score } from '../../utils/format'
defineProps<{ value?: number | null; threshold?: number | null }>()
</script>
<template>
  <div class="cell-bar">
    <span class="track"
      ><span
        class="fill"
        :class="threshold == null || value == null ? '' : value >= threshold ? 'hi' : 'lo'"
        :style="{ width: `${Math.max(0, Math.min(1, value ?? 0)) * 100}%` }" /></span
    ><span class="v">{{ score(value) }}</span>
  </div>
</template>
