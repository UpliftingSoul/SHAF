/** 页面格式化：缺失值保留未知语义，禁止把尚未产生的指标显示成零。 */
export function time(value?: string | null) { return value ? value.replace('T', ' ').slice(0, 19) : '—' }
export function score(value?: number | null, digits = 3) { return value == null ? '—' : value.toFixed(digits) }
export function percent(value?: number | null, digits = 1) { return value == null ? '—' : `${(value * 100).toFixed(digits)}%` }
export function bytes(value?: number | null) {
  if (value == null) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(2)} GB`
}
