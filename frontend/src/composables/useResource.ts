/** 页面取数状态：最后一次请求生效；卸载取消，避免旧响应覆盖新筛选条件。 */
import { onScopeDispose, ref, shallowRef } from 'vue'
import { request } from '../api/http'

export function useResource<T>() {
  const data = shallowRef<T | null>(null)
  const loading = ref(false)
  const error = ref('')
  let generation = 0
  let controller: AbortController | undefined
  async function load(path: string) {
    const version = ++generation
    controller?.abort()
    controller = new AbortController()
    loading.value = true
    error.value = ''
    try {
      const value = await request<T>(path, { signal: controller.signal })
      if (version === generation) data.value = value
    } catch (cause) {
      if (version === generation && !controller.signal.aborted)
        error.value = (cause as Error).message
    } finally {
      if (version === generation) loading.value = false
    }
  }
  onScopeDispose(() => {
    generation++
    controller?.abort()
  })
  return { data, loading, error, load }
}
