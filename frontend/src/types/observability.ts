/** 观测报表契约：三块分别来自磁盘、评估表和校准产物，互不连坐。 */
import type { JobSpec } from './jobs'
export interface ReportBlock {
  present: boolean
  status: 'ok' | 'missing' | 'error'
  hint?: string
  note?: string
  read_note?: string | null
  job: JobSpec
}
export interface CostRow {
  intent: string
  count: number
  tokens: number
  avg_tokens: number
  share: number
}
export interface ObservabilityData {
  cost: ReportBlock & {
    rows?: CostRow[]
    top?: CostRow | null
    total_tokens?: number
    total_requests?: number
    meta?: { days: number; generated_at: string }
  }
  trend: ReportBlock & {
    metric_names?: string[]
    runs?: {
      id: number
      triggered_by: string
      dataset_size: number
      metrics: Record<string, number | null>
      created_at: string | null
    }[]
  }
  calibration: ReportBlock & {
    in_use: number
    in_sync?: boolean
    weights: Record<string, number>
    distribution?: Record<string, Record<string, number>>
    recommended?: { threshold: number; youden_j: number; pass_rate: number; leak_rate: number }
    scan?: { t: number; pass_rate: number; leak_rate: number }[]
  }
}
