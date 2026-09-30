/** 飞轮审核浏览器回归：写回失败保留人工答案，成功后显示新队列状态。 */
import { expect, test } from '@playwright/test'

test('详情三态与审核失败重试', async ({ page }) => {
  const item = {
    id: 3,
    normalized_question: '能水洗吗',
    ai_suggested_answer: '请看说明',
    occurrence_count: 4,
    review_status: '待审',
    created_at: null,
  }
  await page.route('**/api/review/queue**', (route) => route.fulfill({ json: { items: [item] } }))
  await page.route('**/api/review/3', (route) =>
    route.fulfill({
      json: {
        ...item,
        approved_answer: null,
        raws: [
          { raw_question: '没查过', source: 'user_feedback', retrieved_chunks: null },
          { raw_question: '没找到', source: 'retrieval_low_conf', retrieved_chunks: [] },
          {
            raw_question: '查到过',
            source: 'self_check',
            retrieved_chunks: [{ question: '水洗', answer: '已存在知识', rerank_score: 0.35 }],
          },
        ],
      },
    }),
  )
  let attempts = 0
  await page.route('**/api/review/3/approve', (route) => {
    attempts++
    expect(route.request().postDataJSON()).toEqual({ approved_answer: '外套可水洗，垫芯不能机洗' })
    if (attempts === 1)
      return route.fulfill({ status: 502, json: { detail: '写回知识库失败，状态未变可重试' } })
    item.review_status = '通过'
    return route.fulfill({ json: { ok: true, chunk_ids: [71] } })
  })
  await page.goto('/review')
  await page.getByRole('button', { name: '能水洗吗' }).click()
  await expect(page.getByText('该入口未走检索，无召回快照')).toBeVisible()
  await expect(page.getByText('已检索，零命中', { exact: false })).toBeVisible()
  await expect(page.getByText('已存在知识')).toBeVisible()
  await page.getByRole('button', { name: '通过', exact: true }).last().click()
  await page.getByRole('textbox', { name: '核准答案' }).fill('外套可水洗，垫芯不能机洗')
  await page.getByRole('button', { name: '确认通过' }).click()
  await expect(page.getByRole('alert')).toContainText('状态未变可重试')
  await expect(page.getByRole('textbox', { name: '核准答案' })).toHaveValue(
    '外套可水洗，垫芯不能机洗',
  )
  await page.getByRole('button', { name: '确认通过' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('status')).toContainText('已写回知识库')
  expect(attempts).toBe(2)
  await page.screenshot({ path: 'test-results/review-desktop.png' })
})
