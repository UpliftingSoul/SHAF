<!-- 白名单作业按钮与日志：各后台页面复用，卸载时结束轮询。 -->
<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useJob } from '../../composables/useJob'
import { jobLabels, type JobSpec } from '../../types/jobs'
const props = defineProps<{ spec: JobSpec }>()
const emit = defineEmits<{ finish: [] }>()
const { state, error, log, pending, running, start, stop, retryPolling } = useJob(
  () => props.spec,
  () => emit('finish'),
)
const logbox = ref<HTMLElement>()
watch(log, async () => {
  await nextTick()
  if (logbox.value) logbox.value.scrollTop = logbox.value.scrollHeight
})
</script>

<template>
  <div class="job-control">
    <div class="jobrow">
      <button
        class="btn sm go"
        :title="`${spec.cmd}（${spec.needs}）`"
        :disabled="pending || running"
        @click="start"
      >
        {{
          pending ? '处理中…' : running ? '运行中…' : state.status === 'idle' ? '重跑' : '再跑一次'
        }}
        {{ spec.title }}</button
      ><button v-if="running" class="btn sm" :disabled="pending" @click="stop">停止</button>
      <span class="needs"
        >{{ jobLabels[state.status]
        }}{{ state.returncode == null ? '' : `（退出码 ${state.returncode}）` }}</span
      >
    </div>
    <p v-if="error" class="err" role="alert">
      {{ error }} <button v-if="running" class="btn sm" @click="retryPolling">继续查看状态</button>
    </p>
    <pre v-if="log" ref="logbox" class="logbox on">{{ log }}</pre>
  </div>
</template>
