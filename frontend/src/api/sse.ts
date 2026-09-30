/** POST SSE 协议：跨字节解码、逐帧分发、显式结束及取消；不把断流当成成功。 */
export interface StreamFrame {
  event: string
  data: string
}

export class FrameDecoder {
  private buffer = ''
  // 先按行分割，保留末尾半行；CRLF 可以跨两次网络读取。
  private lines: string[] = []
  feed(text: string, final = false): StreamFrame[] {
    this.buffer += text
    const frames: StreamFrame[] = []
    let end: number
    while ((end = this.buffer.indexOf('\n')) >= 0) {
      this.line(this.buffer.slice(0, end).replace(/\r$/, ''), frames)
      this.buffer = this.buffer.slice(end + 1)
    }
    if (final) {
      if (this.buffer) this.line(this.buffer.replace(/\r$/, ''), frames)
      this.buffer = ''
      this.line('', frames)
    }
    return frames
  }
  private line(line: string, output: StreamFrame[]) {
    if (line) {
      this.lines.push(line)
      return
    }
    let event = 'message'
    const data: string[] = []
    for (const item of this.lines) {
      const colon = item.indexOf(':')
      const key = colon < 0 ? item : item.slice(0, colon)
      const value = colon < 0 ? '' : item.slice(colon + 1).replace(/^ /, '')
      if (key === 'event') event = value
      if (key === 'data') data.push(value)
    }
    this.lines = []
    if (data.length) output.push({ event, data: data.join('\n') })
  }
}

export async function streamPost(
  path: string,
  body: unknown,
  signal: AbortSignal,
  onFrame: (payload: Record<string, unknown>) => void,
): Promise<void> {
  const response = await fetch(path, {
    method: 'POST',
    signal,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new Error(
      typeof error.detail === 'string' ? error.detail : `请求失败（HTTP ${response.status}）`,
    )
  }
  if (!response.body) throw new Error('浏览器无法读取流式响应')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const frames = new FrameDecoder()
  let finished = false
  const dispatch = (items: StreamFrame[]) => {
    for (const item of items) {
      if (item.data === '[DONE]') {
        finished = true
        continue
      }
      const payload = JSON.parse(item.data) as Record<string, unknown>
      if (item.event === 'error') throw new Error(String(payload.message || '流式请求失败'))
      onFrame(payload)
      if (payload.event === 'interrupt') finished = true
    }
  }
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        dispatch(frames.feed(decoder.decode(), true))
        break
      }
      dispatch(frames.feed(decoder.decode(value, { stream: true })))
    }
    if (!finished) throw new Error('连接提前结束，请重试或查看会话历史')
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
