/** 会话回归：取消后旧响应隔离、明确取消工单，以及历史失败后的可重试行为。 */
import { effectScope } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useChat } from '../../src/composables/useChat'
import { streamPost } from '../../src/api/sse'
import { request } from '../../src/api/http'

vi.mock('../../src/api/sse', () => ({ streamPost: vi.fn() }))
vi.mock('../../src/api/http', () => ({ request: vi.fn(), post: vi.fn(), query: () => '' }))
const scopes: ReturnType<typeof effectScope>[] = []
function setup() {
  localStorage.clear()
  vi.mocked(request).mockResolvedValue({ items: [] })
  const scope = effectScope()
  scopes.push(scope)
  return scope.run(useChat)!
}
afterEach(() => { scopes.splice(0).forEach(scope => scope.stop()); vi.clearAllMocks() })

describe('聊天会话隔离', () => {
  it('新会话后旧流不能改会话号或内容', async () => {
    const chat = setup()
    let callback!: Parameters<typeof streamPost>[3]
    let finish!: () => void
    vi.mocked(streamPost).mockImplementation(async (_path, _body, _signal, receive) => {
      callback = receive
      await new Promise<void>(resolve => { finish = resolve })
    })
    const sending = chat.send('运费多少')
    chat.newConversation()
    callback({ event: 'done', conversation_id: 91 })
    callback({ delta: '旧回答' })
    finish()
    await sending
    expect(chat.conversationId.value).toBeNull()
    expect(chat.messages.value).toEqual([])
    expect(localStorage.getItem('mewhelp_conversation_id')).toBeNull()
  })
  it('取消工单仍传 confirmed=false，重复点击不会创建第二个请求', async () => {
    const chat = setup()
    const source = chat.addMessage('bot', '')
    source.conversationId = 13
    source.interrupt = { kind: 'confirm_ticket', preview: { ticket_type: '咨询', description: '问题' } }
    vi.mocked(streamPost).mockResolvedValue()
    await chat.resume(source, { confirmed: false })
    await chat.resume(source, { confirmed: true })
    expect(streamPost).toHaveBeenCalledTimes(1)
    expect(streamPost).toHaveBeenCalledWith('/api/actions/resume', { conversation_id: 13, confirmed: false }, expect.any(AbortSignal), expect.any(Function))
  })
  it('历史取数失败保留错误并可重新加载同一会话', async () => {
    const chat = setup()
    vi.mocked(request).mockRejectedValueOnce(new Error('数据库暂时不可用'))
    await chat.switchConversation(13)
    expect(chat.historyError.value).toBe('数据库暂时不可用')
    vi.mocked(request).mockResolvedValue({ items: [{ role: 'user', content: '物流到哪了' }, { role: 'assistant', content: '请提供订单号' }] })
    await chat.switchConversation(13)
    expect(chat.historyError.value).toBe('')
    expect(chat.messages.value.map(message => message.text)).toEqual(['物流到哪了', '请提供订单号'])
  })
})
