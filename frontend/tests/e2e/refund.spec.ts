/** 退款安全交互回归：选单续跑、订单只读、原因人工选择、写入失败后保留输入。 */
import { expect, test } from '@playwright/test'

test('选单续跑后由用户明确提交退款，失败可重试', async ({ page }) => {
  await page.route('**/api/conversations**', (route) => route.fulfill({ json: { items: [] } }))
  await page.route('**/api/chat', (route) =>
    route.fulfill({
      contentType: 'text/event-stream',
      body: 'data: {"event":"interrupt","kind":"select_order","conversation_id":13,"orders":[{"order_id":"1001","product":"猫粮","status":"已签收","amount":99}]}\n\ndata: [DONE]\n\n',
    }),
  )
  await page.route('**/api/actions/resume', (route) => {
    expect(route.request().postDataJSON()).toEqual({ conversation_id: 13, order_id: '1001' })
    return route.fulfill({
      contentType: 'text/event-stream',
      body: 'data: {"delta":"可以申请退款"}\n\ndata: {"event":"actions","items":[{"type":"refund_form","draft":{"order_id":"1001"}}]}\n\ndata: {"event":"done","conversation_id":13}\n\ndata: [DONE]\n\n',
    })
  })
  let attempts = 0
  await page.route('**/api/actions/create-refund', (route) => {
    attempts++
    expect(route.request().postDataJSON()).toEqual({
      conversation_id: 13,
      order_id: '1001',
      reason: '七天无理由',
    })
    return attempts === 1
      ? route.fulfill({ status: 502, json: { detail: '登记失败，请重试' } })
      : route.fulfill({ json: { ticket_no: 'R0001' } })
  })
  await page.goto('/')
  await page.locator('#input').fill('我要退款')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  await page.getByRole('button', { name: '订单 1001', exact: false }).click()
  await page.getByRole('button', { name: '提交退款工单', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByLabel('订单号')).toHaveAttribute('readonly', '')
  await page.getByRole('button', { name: '提交退款', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('请选择退款原因')
  expect(attempts).toBe(0)
  await page.getByLabel('退款原因', { exact: false }).selectOption('七天无理由')
  await page.getByRole('button', { name: '提交退款', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('登记失败')
  await expect(page.getByLabel('退款原因', { exact: false })).toHaveValue('七天无理由')
  await page.getByRole('button', { name: '提交退款', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByText('退款申请已提交:R0001')).toBeVisible()
  await expect(page.getByRole('button', { name: '提交退款工单', exact: true })).toBeDisabled()
  expect(attempts).toBe(2)
})
