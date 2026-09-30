/** RAG 报告与跨轮台账契约；未知指标保持 null，不折算为零分。 */
import type { JobSpec } from './jobs'
export const strategies = [
  { key: 'vector', label: '纯向量', color: '#7fc4e8' },
  { key: 'bm25', label: '纯 BM25', color: '#5fcf6a' },
  { key: 'hybrid', label: '混合', color: '#ff8095' },
  { key: 'hybrid_rerank', label: '混合 + 重排', color: '#ff9f57' },
] as const
export const buckets = [
  { key: 'A_policy', label: '政策类' },
  { key: 'B_model', label: '型号类' },
  { key: 'C_colloquial', label: '口语类' },
  { key: 'E_multi', label: '跨文档类' },
  { key: 'overall', label: '总体' },
] as const
export type RagMetric = 'MRR' | 'Recall@5' | '证据覆盖度' | '答案覆盖度'
export interface FaithSnapshot {
  id: string
  bucket: string
  query: string
  answer: string
  reason: string
  strategy?: string
}
export interface GenerationReport {
  answer_coverage: Record<string, Record<string, number | null>>
  faithfulness: Record<string, { v: number | null; answered: number }>
  faithfulness_cases: FaithSnapshot[]
  refusal: {
    rate: number | null
    correct: number
    total: number
    skipped_ids?: string[]
    cases?: { id: string; query: string; section_path: string; evidence: string }[]
  }
  skipped?: number
}
export interface RagReport {
  present: boolean
  hint?: string
  make?: string
  meta?: { n_samples?: number; kb_chunks?: number; chat_model?: string; generated_at?: string }
  retrieval?: Record<string, Record<string, { mrr?: number | null; recall?: number | null }>>
  evidence_coverage?: Record<string, Record<string, number | null>>
  generation?: GenerationReport | null
  generation_done?: boolean
  read_notes?: Record<string, string>
  best?: { strategy: string | null; mrr: number | null }
  job: {
    specs: JobSpec[]
    artifacts: {
      json: { path: string; present: boolean; mtime?: string }
      text: { path: string; present: boolean; mtime?: string }
    }
  }
}
export function metricValue(
  report: RagReport,
  metric: RagMetric,
  strategy: string,
  bucket: string,
): number | null {
  if (metric === 'MRR') return report.retrieval?.[strategy]?.[bucket]?.mrr ?? null
  if (metric === 'Recall@5') return report.retrieval?.[strategy]?.[bucket]?.recall ?? null
  if (metric === '证据覆盖度') return report.evidence_coverage?.[strategy]?.[bucket] ?? null
  return report.generation?.answer_coverage?.[strategy]?.[bucket] ?? null
}
export type FaithStatus = '未解决' | '已解决' | '无需解决'
export interface FaithCase {
  id: number
  eval_id: string
  bucket: string
  query: string
  strategy: string
  answer: string
  reason: string
  citations:
    | { n: number; question?: string; answer?: string; section_path?: string; chunk_id?: string }[]
    | null
  judge_model: string | null
  resolution: string | null
  status: FaithStatus
  seen_count: number
  reopened: boolean
  first_seen_at: string | null
  last_seen_at: string | null
  resolved_at: string | null
}
export interface FaithLedger {
  items: FaithCase[]
  total: number
  page: number
  pages: number
  size: number
  counts: Record<FaithStatus, number>
  hallucination: {
    evaluated: number | null
    graded: number | null
    absent: number | null
    cases_judged: number
    cases_confirmed: number
    pending: number
    dismissed: number
    refusal_missed: number
    judged: number
    confirmed: number
    judged_rate: number | null
    confirmed_rate: number | null
    ledger: Record<FaithStatus, number> & { total: number }
  }
}
