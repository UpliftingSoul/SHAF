/** 聊天视图契约：与后端 SSE、动作接口及历史消息的实际字段一致。 */
export interface Order { order_id: string; product?: string; status?: string; amount?: number }
export interface Citation { n: number; question?: string; answer?: string; section_path?: string; content_type?: string }
export interface Action {
  type: 'transfer_human' | 'create_ticket' | 'refund_form' | 'select_order'
  draft?: { order_id?: string }
  orders?: Order[]
  used?: boolean
}
export interface Interruption {
  kind: 'select_order' | 'confirm_ticket'
  orders?: Order[]
  preview?: { ticket_type?: string; description?: string }
  decided?: boolean
  picked?: string
}
export interface ChatMessage {
  id: number
  role: 'user' | 'bot'
  text: string
  question: string
  conversationId: number | null
  tools: string[]
  citations: Citation[]
  actions: Action[]
  interrupt?: Interruption
  streaming: boolean
  feedback?: 'up' | 'down'
  error?: boolean
  plain?: boolean
}
export interface Conversation { id: number; preview: string; has_summary: boolean }
