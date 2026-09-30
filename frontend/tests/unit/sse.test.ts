/** SSE 回归：测试真实分块边界与错误语义，不依赖数据库或上游模型。 */
import { describe, expect, it, vi } from 'vitest'
import { FrameDecoder, streamPost } from '../../src/api/sse'

describe('流式协议', () => {
  it('处理 CRLF 分块、多行数据、注释和最后一帧', () => {
    const decoder = new FrameDecoder()
    expect(decoder.feed(': keepalive\r\ndata: 第一行\r')).toEqual([])
    expect(decoder.feed('\ndata: 第二行\r\n\r\nevent: error\ndata: 尾帧', true)).toEqual([
      { event: 'message', data: '第一行\n第二行' }, { event: 'error', data: '尾帧' },
    ])
  })
  function mockStream(content: string, split: number) {
    const bytes = new TextEncoder().encode(content)
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new ReadableStream({
      start(controller) { controller.enqueue(bytes.slice(0, split)); controller.enqueue(bytes.slice(split)); controller.close() },
    }))))
  }
  it('跨中文 UTF-8 字节分块仍正确恢复', async () => {
    mockStream('data: {"delta":"中文"}\n\ndata: [DONE]\n\n', 18)
    const received = vi.fn()
    await streamPost('/api/chat', {}, new AbortController().signal, received)
    expect(received).toHaveBeenCalledWith({ delta: '中文' })
  })
  it('断流报错，interrupt 允许等待用户续跑', async () => {
    mockStream('data: {"delta":"片段"}\n\n', 10)
    await expect(streamPost('/api/chat', {}, new AbortController().signal, vi.fn())).rejects.toThrow('连接提前结束')
    mockStream('data: {"event":"interrupt","kind":"select_order"}\n\n', 10)
    await expect(streamPost('/api/chat', {}, new AbortController().signal, vi.fn())).resolves.toBeUndefined()
  })
  it('服务端错误帧保留面向用户的提示', async () => {
    mockStream('event: error\ndata: {"message":"会话不存在"}\n\n', 10)
    await expect(streamPost('/api/chat', {}, new AbortController().signal, vi.fn())).rejects.toThrow('会话不存在')
  })
})
