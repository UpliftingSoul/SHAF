<!-- 服务端分页控件：使用响应中的裁剪页码，上一页/下一页不在客户端筛选数据。 -->
<script setup lang="ts">
defineProps<{ page: number; pages: number; total: number; size: number; disabled?: boolean }>()
const emit = defineEmits<{ change: [page: number] }>()
</script>
<template>
  <div class="pager">
    <button class="btn sm" :disabled="disabled || page <= 1" @click="emit('change', page - 1)">
      ← 上一页</button
    ><button class="btn sm" :disabled="disabled || page >= pages" @click="emit('change', page + 1)">
      下一页 →
    </button>
    <div class="info">
      第 <b>{{ page }}</b> / {{ pages }} 页 · 共 <b>{{ total }}</b> 条 · 每页 {{ size }} 条
    </div>
  </div>
</template>
<style scoped>
.pager {
  margin-top: 16px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.info {
  font-size: 12.5px;
}
.info b {
  font-variant-numeric: tabular-nums;
}
</style>
