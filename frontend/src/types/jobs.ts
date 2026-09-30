/** 后台作业契约：命令、前置条件与状态全部由后端白名单提供。 */
export interface JobSpec {
  name: string
  title: string
  cmd: string
  needs: string
  heavy: boolean
  status: 'idle' | 'running' | 'ok' | 'failed' | 'stopped'
  returncode: number | null
  log?: string
}
export const jobLabels = {
  idle: '未运行',
  running: '运行中',
  ok: '完成',
  failed: '失败',
  stopped: '已停止',
}
