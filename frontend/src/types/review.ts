/** 飞轮审核契约：队列行、归并原话与当时检索快照。 */
export interface ReviewItem { id: number; normalized_question: string; ai_suggested_answer: string | null; occurrence_count: number; review_status: '待审' | '通过' | '驳回'; created_at: string | null }
export interface ReviewDetail extends ReviewItem {
  approved_answer: string | null
  raws: { raw_question: string; source: string; reason: string | null; created_at: string | null;
    retrieved_chunks: { question?: string; answer?: string; rerank_score?: number; section_path?: string }[] | null }[]
}
