/** JSON 请求边界：统一错误信息、取消信号与查询参数，不向浏览器提供后端密钥。 */
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message) }
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, options)
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = body?.detail
    const message = typeof detail === 'string' ? detail
      : Array.isArray(detail) ? detail.map((item: { msg: string }) => item.msg).join('；')
      : `请求失败（HTTP ${response.status}）`
    throw new ApiError(message, response.status)
  }
  return body as T
}

export function post<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  return request<T>(path, {
    method: 'POST', signal,
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  })
}

export function query(values: Record<string, string | number | boolean | null | undefined>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    if (value !== null && value !== undefined) params.set(key, String(value))
  }
  const result = params.toString()
  return result ? `?${result}` : ''
}
