/** 旁路分类结果契约：多标签计入每个类目，问题原话与标准化问句同时保留。 */
export interface TopicDistribution {
  total: number
  latest: string | null
  classes: { label: string; count: number; samples: string[] }[]
}
export interface TopicQuestions {
  label: string
  page: number
  pages: number
  total: number
  size: number
  items: {
    question_id: number
    text: string
    labels: string[]
    source: string
    occurrence_count: number | null
    review_status: string | null
    classified_at: string
    normalized: boolean
    raw_question: string
  }[]
}
