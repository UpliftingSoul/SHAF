<!-- 一组作业入口：只负责分组，具体运行状态和日志由各 JobButton 持有。 -->
<script setup lang="ts">
import JobButton from './JobButton.vue'
import type { JobSpec } from '../../types/jobs'
defineProps<{ jobs: JobSpec[] }>()
const emit = defineEmits<{ finish: [] }>()
</script>
<template>
  <div class="jobs">
    <JobButton v-for="job in jobs" :key="job.name" :spec="job" @finish="emit('finish')" />
  </div>
</template>
<style scoped>
.jobs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
  align-items: flex-start;
}
.jobs :deep(.job-control) {
  min-width: 0;
  max-width: 100%;
}
.jobs :deep(.logbox) {
  max-width: calc(100vw - 80px);
}
</style>
