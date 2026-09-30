/** 会话状态机：发送、续跑、历史隔离与用户反馈；所有请求随页面卸载取消。 */
import { onScopeDispose, ref } from 'vue'
import { post, query, request } from '../api/http'
import { streamPost } from '../api/sse'
import type { Action, ChatMessage, Citation, Conversation, Interruption } from '../types/chat'

const SESSION_KEY = 'mewhelp_session_id'
const CONV_KEY = 'mewhelp_conversation_id'
const replayActions = new Map<string, Action[]>([
  [
    '抱歉,这个问题我暂时没有查到确切信息,不敢乱答。建议您联系人工客服进一步确认,以免给您错误的指引。',
    [{ type: 'transfer_human' }],
  ],
  [
    '非常抱歉给您带来了不好的体验,我理解您的心情。您可以选择转接人工客服,或让我为您登记一张工单跟进处理。',
    [{ type: 'transfer_human' }, { type: 'create_ticket' }],
  ],
])

export function useChat() {
  let userId = localStorage.getItem(SESSION_KEY)
  if (!userId) {
    userId = crypto.randomUUID()
    localStorage.setItem(SESSION_KEY, userId)
  }
  const storedId = Number(localStorage.getItem(CONV_KEY))
  const conversationId = ref<number | null>(
    storedId > 0 && Number.isInteger(storedId) ? storedId : null,
  )
  const conversations = ref<Conversation[]>([])
  const messages = ref<ChatMessage[]>([])
  const busy = ref(false)
  const historyError = ref('')
  let sequence = 0
  let generation = 0
  let listGeneration = 0
  let controller: AbortController | undefined
  const listController = new AbortController()

  function storeId(id: number | null) {
    conversationId.value = id
    if (id === null) localStorage.removeItem(CONV_KEY)
    else localStorage.setItem(CONV_KEY, String(id))
  }
  function addMessage(role: 'user' | 'bot', text: string, question = '', plain = false) {
    messages.value.push({
      id: ++sequence,
      role,
      text,
      question,
      conversationId: conversationId.value,
      tools: [],
      citations: [],
      actions: [],
      streaming: false,
      plain,
    })
    return messages.value[messages.value.length - 1]!
  }
  async function refreshConversations() {
    const version = ++listGeneration
    try {
      const result = await request<{ items: Conversation[] }>(
        `/api/conversations${query({ user_id: userId })}`,
        { signal: listController.signal },
      )
      if (version === listGeneration) conversations.value = result.items
    } catch {
      /* 侧栏取数失败不打断当前聊天。 */
    }
  }
  function reset() {
    generation++
    controller?.abort()
    busy.value = false
    historyError.value = ''
    messages.value = []
  }
  function newConversation() {
    reset()
    storeId(null)
    void refreshConversations()
  }

  // 流和历史共享世代号：切换后旧异步回调不能写入新会话。
  async function run(path: string, body: unknown, question: string) {
    const version = ++generation
    controller?.abort()
    const current = new AbortController()
    controller = current
    busy.value = true
    const message = addMessage('bot', '', question)
    message.streaming = true
    try {
      await streamPost(path, body, current.signal, (frame) => {
        if (version !== generation) return
        if (typeof frame.conversation_id === 'number') {
          storeId(frame.conversation_id)
          message.conversationId = frame.conversation_id
        }
        if (typeof frame.delta === 'string') message.text += frame.delta
        if (frame.event === 'tool') message.tools.push(String(frame.name))
        if (frame.event === 'citations') message.citations = frame.items as Citation[]
        if (frame.event === 'actions') message.actions = frame.items as Action[]
        if (frame.event === 'interrupt') message.interrupt = frame as unknown as Interruption
      })
      if (!message.text && !message.interrupt) message.text = '(无回复)'
    } catch (cause) {
      if (version === generation && !current.signal.aborted) {
        message.error = true
        message.text = (cause as Error).message || '回复失败,请稍后重试'
        message.actions = []
        message.interrupt = undefined
      }
    } finally {
      if (version === generation) {
        message.streaming = false
        busy.value = false
        void refreshConversations()
      }
    }
  }
  async function send(text: string) {
    const content = text.trim()
    if (busy.value || !content) return
    historyError.value = ''
    addMessage('user', content)
    await run(
      '/api/chat',
      { user_id: userId, message: content, conversation_id: conversationId.value },
      content,
    )
  }
  async function resume(source: ChatMessage, value: { order_id: string } | { confirmed: boolean }) {
    if (busy.value || !source.interrupt || source.interrupt.decided || !source.conversationId)
      return
    source.interrupt.decided = true
    if ('order_id' in value) source.interrupt.picked = value.order_id
    const text =
      'order_id' in value
        ? `选择订单 ${value.order_id}`
        : value.confirmed
          ? '确认提交工单'
          : '取消建单'
    addMessage('user', text)
    await run('/api/actions/resume', { conversation_id: source.conversationId, ...value }, text)
  }
  async function switchConversation(id: number) {
    if (id === conversationId.value && messages.value.length) return
    reset()
    storeId(id)
    const version = generation
    const current = new AbortController()
    controller = current
    busy.value = true
    try {
      const result = await request<{ items: { role: string; content: string }[] }>(
        `/api/conversations/${id}/messages`,
        { signal: current.signal },
      )
      if (version !== generation) return
      let question = ''
      for (const item of result.items) {
        if (!item.content) continue
        const role = item.role === 'user' ? 'user' : 'bot'
        if (role === 'user') question = item.content
        const message = addMessage(role, item.content, question)
        message.actions = (replayActions.get(item.content.trim()) || []).map((action) => ({
          ...action,
        }))
      }
    } catch (cause) {
      if (version === generation && !current.signal.aborted)
        historyError.value = (cause as Error).message
    } finally {
      if (version === generation) busy.value = false
      void refreshConversations()
    }
  }
  function feedback(message: ChatMessage, rating: 'up' | 'down') {
    if (message.feedback || !message.conversationId) return
    message.feedback = rating
    void post('/api/feedback', {
      conversation_id: message.conversationId,
      rating,
      question: message.question,
    }).catch(() => {})
  }
  function transfer(action: Action) {
    if (action.used) return
    action.used = true
    addMessage('bot', '已转接人工客服', '', true)
    addMessage('bot', '您好,我是客服小猫,请问有什么可以帮您的', '', true)
  }
  onScopeDispose(() => {
    generation++
    listGeneration++
    controller?.abort()
    listController.abort()
  })
  return {
    conversationId,
    conversations,
    messages,
    busy,
    historyError,
    send,
    resume,
    feedback,
    refreshConversations,
    switchConversation,
    newConversation,
    addMessage,
    transfer,
  }
}
