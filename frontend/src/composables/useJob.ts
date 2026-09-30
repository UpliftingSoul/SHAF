/** 单个任务生命周期：串行轮询、重任务确认、停止与卸载取消，完成状态独立于页面刷新。 */
import {
  computed,
  onScopeDispose,
  ref,
  shallowRef,
  watch,
  type MaybeRefOrGetter,
  toValue,
} from 'vue'
import { post, request } from '../api/http'
import type { JobSpec } from '../types/jobs'

export function useJob(spec: MaybeRefOrGetter<JobSpec>, onFinish: () => void) {
  const state = shallowRef(toValue(spec))
  const error = ref('')
  const log = ref('')
  const pending = ref(false)
  const running = computed(() => state.value.status === 'running')
  let timer: ReturnType<typeof setTimeout> | undefined
  let controller = new AbortController()
  let disposed = false
  let version = 0
  function clear() {
    clearTimeout(timer)
    timer = undefined
  }
  function paint(value: JobSpec) {
    state.value = value
    if (value.log) log.value = value.log
  }
  function schedule() {
    clear()
    if (!disposed && running.value) timer = setTimeout(() => void poll(), 1200)
  }
  async function poll() {
    const current = version
    try {
      const value = await request<JobSpec>(`/api/jobs/${state.value.name}`, {
        signal: controller.signal,
      })
      if (disposed || current !== version) return
      paint(value)
      if (running.value) schedule()
      else onFinish()
    } catch (cause) {
      if (!disposed && current === version) error.value = `轮询失败：${(cause as Error).message}`
    }
  }
  async function start() {
    if (pending.value || running.value) return
    const value = state.value
    if (
      value.heavy &&
      !window.confirm(
        `「${value.title}」是分钟级重活（${value.cmd}）。\n前置：${value.needs}\n确认现在跑？`,
      )
    )
      return
    clear()
    version++
    controller.abort()
    controller = new AbortController()
    pending.value = true
    error.value = ''
    log.value = '发起中…'
    try {
      const result = await post<JobSpec>(`/api/jobs/${value.name}`, undefined, controller.signal)
      if (disposed) return
      paint(result)
      if (running.value) schedule()
      else onFinish()
    } catch (cause) {
      if (!disposed) error.value = `发起失败：${(cause as Error).message}`
    } finally {
      if (!disposed) pending.value = false
    }
  }
  async function stop() {
    if (!running.value || pending.value) return
    pending.value = true
    clear()
    version++
    controller.abort()
    controller = new AbortController()
    error.value = ''
    try {
      paint(await post<JobSpec>(`/api/jobs/${state.value.name}/stop`, undefined, controller.signal))
      if (!disposed) {
        if (running.value) schedule()
        else onFinish()
      }
    } catch (cause) {
      if (!disposed) {
        error.value = (cause as Error).message
        schedule()
      }
    } finally {
      if (!disposed) pending.value = false
    }
  }
  function retryPolling() {
    error.value = ''
    clear()
    void poll()
  }
  watch(
    () => toValue(spec),
    (value) => {
      // 父页面刷新不能覆盖一次已发起请求；已有轮询也不因重复取数另起一个。
      if (pending.value) return
      const wasRunning = running.value
      paint(value)
      if (running.value && !wasRunning) schedule()
      if (!running.value) clear()
    },
  )
  schedule()
  onScopeDispose(() => {
    disposed = true
    version++
    clear()
    controller.abort()
  })
  return { state, error, log, pending, running, start, stop, retryPolling }
}
