/** 知识库页面数据契约：MySQL 权威原文、Milvus 状态、切块与人工暂存审核。 */
import type { JobSpec } from './jobs'
export interface ChunkView { seq: number; section_path: string; category: string; questions: string; answer: string; chars: number; is_key_clause: boolean; is_table: boolean; duplicate: boolean | null }
export interface Features { sections: number; table_split: boolean; overlap: boolean }
export interface Preview { source: string; total: number; duplicates: number; key_clause: number; dedup_known: boolean; features: Features; chunks: ChunkView[] }
export interface ChunkStats { total: number | null; pending: number | null; done: number | null; key_clause: number | null }
export interface StagingRow { id: number; batch_no: number; source_ref: string | null; question: string; answer: string }
export type StagingStatus = 'kept' | 'extracted' | 'discarded' | 'approved' | 'rejected'
export interface StagingStats { total: number; counts: Record<StagingStatus, number>; batches: number; latest_batch: number | null }
export interface StagingData { stats: StagingStats; rows: Record<StagingStatus, StagingRow[]> }
export interface KnowledgeOverview {
  chunks: ChunkStats; db_error: string | null; consistent: boolean | null
  milvus: { online: boolean; count: number | null; collection: string; detail?: string }
  staging: StagingStats | null
  content_types: { key: string; desc: string }[]
  sources: { file: string; path: string; content_type: string; present: boolean; chars?: number; lines?: number; chunks?: number; key_clause?: number; features?: Features }[]
  jobs: JobSpec[]
  recent: { id: number; questions: string; answer: string; category: string; section_path: string; content_type: string; is_key_clause: boolean; status: string; created_at: string }[]
}
export interface RetrievalHit { id: number; question: string; answer: string; score: number; rerank_score: number | null; section_path: string; content_type: string; category: string }
export interface SearchResult { q: string; strategy: string; hits: RetrievalHit[] }
