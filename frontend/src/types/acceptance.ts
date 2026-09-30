/** 分类器验收契约：只展示后端读取的真实报告，缺产物通过 present 显式区分。 */
import type { JobSpec } from './jobs'

export interface ReportState {
  present: boolean
  make?: string
  hint?: string
}
export interface ClassifierState {
  online: boolean
  detail: string
}
export interface AcceptanceBlock {
  key: string
  no: number
  title: string
  page: string | null
  status: 'pass' | 'fail' | 'missing'
  headline: string
  note: string
  jobs: string[]
}
export interface AcceptanceOverview {
  blocks: AcceptanceBlock[]
  passed: number
  total: number
  all_pass: boolean
  classifier: ClassifierState
  jobs: JobSpec[]
}
export interface EvaluationError {
  text: string
  kind: string
  matrix_entries: number
  missed: string[]
  extra: string[]
  gold: string[]
  pred: string[]
}
export interface ErrorReport {
  eval: ReportState & { ran_at?: string; test_size?: number; threshold?: number }
  errors: EvaluationError[]
  kinds: Record<string, number>
  matrix_entries?: number
  total_fp?: number
  total_fn?: number
  pairs: { missed: string; grabbed: string; count: number; severity: string }[]
  recipes?: Record<string, string>
}
export interface FileStat {
  path: string
  present: boolean
  bytes?: number
  mtime?: string
  lines?: number
}
export type SplitKey = 'train' | 'val' | 'test'
export interface DatasetSplit {
  desc: string
  size: number
  multi_label: number
  counts: Record<string, number>
  file: FileStat
}
export interface AcceptanceData {
  lineage: (FileStat & { file: string; stage: string; desc: string; make: string })[]
  dataset: {
    splits: Record<SplitKey, DatasetSplit>
    leaks: Record<'train_val' | 'train_test' | 'val_test', number>
    clean: boolean
  }
  sample_review: FileStat
  model: { files: FileStat[]; threshold: number | null; threshold_file: FileStat; trio_ok: boolean }
  onnx: {
    files: FileStat[]
    report: ReportState & {
      passed?: boolean
      checked?: number
      mismatch?: number
      opset?: number
      onnx_bytes?: number
      ran_at?: string
    }
  }
  topic_names: string[]
}
export interface AverageMetrics {
  p: number
  r: number
  f1: number
}
export interface ClassMetrics extends AverageMetrics {
  name: string
  severity: string
  support: number
  red_line: number | null
  passed: boolean | null
  tp: number
  tn: number
  fp: number
  fn: number
}
export interface EvaluationReport extends ReportState {
  ran_at?: string
  test_size?: number
  threshold?: number
  micro?: AverageMetrics
  macro?: AverageMetrics
  red_line_passed?: boolean
  classes?: ClassMetrics[]
  total_cells?: number
  total_fp?: number
  total_fn?: number
}
export interface ThresholdReport extends ReportState {
  scan?: { threshold: number; micro_f1: number }[]
  best_threshold?: number
  best_micro_f1?: number
  in_use_threshold?: number | null
  in_use?: number | null
  consistent?: boolean
  val_size?: number
  ran_at?: string
}
export interface AcceptanceEvaluation {
  eval: EvaluationReport
  scan: ThresholdReport
  threshold_in_use: number | null
  severity: Record<string, string>
  classifier: ClassifierState
}
export interface ClassificationResult {
  text: string
  threshold: number | null
  labels: string[]
  scores: { label: string; score: number; hit: boolean }[]
  fallback: boolean
}
