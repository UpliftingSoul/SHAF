/** 作业生命周期回归：串行轮询、失败后续查、重任务拒绝及卸载取消。 */
import { effectScope } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import { useJob } from '../../src/composables/useJob'
import { post, request } from '../../src/api/http'
import type { JobSpec } from '../../src/types/jobs'
vi.mock('../../src/api/http', () => ({ post: vi.fn(), request: vi.fn() }))
const spec: JobSpec = {
  name: 'eval-rag',
  title: '重跑评估',
  cmd: 'make eval-rag',
  needs: 'Milvus',
  heavy: false,
  status: 'idle',
  returncode: null,
}
const scopes: ReturnType<typeof effectScope>[] = []
function setup() {
  vi.useFakeTimers()
  const scope = effectScope()
  scopes.push(scope)
  const finish = vi.fn()
  return { scope, finish, job: scope.run(() => useJob(spec, finish))! }
}
afterEach(() => {
  scopes.splice(0).forEach((scope) => scope.stop())
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.clearAllMocks()
})
it('运行中轮询串行，结束只通知一次', async () => {
  const { job, finish } = setup()
  vi.mocked(post).mockResolvedValue({ ...spec, status: 'running' })
  let resolve!: (value: JobSpec) => void
  vi.mocked(request).mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done
      }),
  )
  await job.start()
  await vi.advanceTimersByTimeAsync(1200)
  await vi.advanceTimersByTimeAsync(6000)
  expect(request).toHaveBeenCalledTimes(1)
  resolve({ ...spec, status: 'ok', returncode: 0, log: '完成' })
  await Promise.resolve()
  await vi.advanceTimersByTimeAsync(6000)
  expect(finish).toHaveBeenCalledTimes(1)
  expect(job.log.value).toBe('完成')
})
it('轮询失败可继续查同一作业，卸载后取消并停止请求', async () => {
  const { job, scope, finish } = setup()
  vi.mocked(post).mockResolvedValue({ ...spec, status: 'running' })
  vi.mocked(request)
    .mockRejectedValueOnce(new Error('网络中断'))
    .mockResolvedValue({ ...spec, status: 'running' })
  await job.start()
  await vi.advanceTimersByTimeAsync(1200)
  expect(job.error.value).toContain('网络中断')
  job.retryPolling()
  await Promise.resolve()
  await Promise.resolve()
  expect(request).toHaveBeenCalledTimes(2)
  const signal = vi.mocked(request).mock.calls[1]![1]!.signal as AbortSignal
  scope.stop()
  await vi.advanceTimersByTimeAsync(6000)
  expect(signal.aborted).toBe(true)
  expect(request).toHaveBeenCalledTimes(2)
  expect(finish).not.toHaveBeenCalled()
})
it('重任务取消确认不会发起作业', async () => {
  vi.useFakeTimers()
  const scope = effectScope()
  scopes.push(scope)
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  const job = scope.run(() => useJob({ ...spec, heavy: true }, vi.fn()))!
  await job.start()
  expect(post).not.toHaveBeenCalled()
})
