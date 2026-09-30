<!-- 报表关键数卡片：只格式化调用方传入的权威值，不设隐含的通过线。 -->
<script setup lang="ts">
defineProps<{ items: { label: string; value: string | number; sub?: string; unit?: string }[] }>()
</script>
<template>
  <div class="kpis">
    <div v-for="item in items" :key="item.label" class="kpi">
      <div class="k-label">{{ item.label }}</div>
      <div class="k-val">
        {{ item.value }}<small v-if="item.unit">{{ item.unit }}</small>
      </div>
      <div class="k-sub">{{ item.sub }}</div>
    </div>
  </div>
</template>
<style scoped>
.kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin: 12px 0 16px;
}
.kpi {
  border: 3px solid var(--ink);
  background: var(--paper);
  padding: 10px 12px;
}
.k-label {
  font-size: 11.5px;
  color: #6d6478;
}
.k-val {
  margin: 3px 0 4px;
  font-size: 24px;
  font-weight: 800;
  line-height: 1.3;
  overflow-wrap: anywhere;
}
.k-val small {
  font-size: 12px;
  margin-left: 4px;
}
.k-sub {
  font-size: 11px;
  color: #6d6478;
  line-height: 1.6;
}
@media (max-width: 760px) {
  .kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
