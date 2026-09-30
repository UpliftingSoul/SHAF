/** 受控浏览器交互：流式聊天及知识输入失效，不宣称真实基础设施联调。 */
import { expect, test } from '@playwright/test'

test('聊天流、引用、反馈和工单取消续跑', async ({ page }) => {
  await page.route('**/api/conversations**', route => route.fulfill({ json: { items: [] } }))
  await page.route('**/api/chat', route => route.fulfill({ contentType: 'text/event-stream', body:
    'data: {"delta":"满99元包邮[1]"}\n\ndata: {"event":"citations","items":[{"n":1,"section_path":"运费政策","answer":"测试来源原文"}]}\n\ndata: {"event":"done","conversation_id":13}\n\ndata: [DONE]\n\n' }))
  await page.route('**/api/feedback', route => route.fulfill({ json: { ok: true } }))
  await page.goto('/')
  await page.getByRole('button', { name: '退换货政策是怎样的?' }).click()
  await expect(page.getByText('满99元包邮', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '查看来源 1' }).click()
  await expect(page.getByText('测试来源原文')).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '这条回答没帮助' }).click()
  await expect(page.getByText('已反馈,谢谢~')).toBeVisible()
  await page.screenshot({ path: 'test-results/chat-desktop.png' })

  await page.route('**/api/chat', route => route.fulfill({ contentType: 'text/event-stream', body:
    'data: {"event":"interrupt","kind":"confirm_ticket","conversation_id":13,"preview":{"ticket_type":"咨询","description":"登记问题"}}\n\ndata: [DONE]\n\n' }))
  let resumeBody: unknown
  await page.route('**/api/actions/resume', route => {
    resumeBody = route.request().postDataJSON()
    return route.fulfill({ contentType: 'text/event-stream', body: 'data: {"delta":"已取消建单"}\n\ndata: {"event":"done","conversation_id":13}\n\ndata: [DONE]\n\n' })
  })
  await page.locator('#input').fill('帮我建工单')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await expect(page.getByText('已取消建单')).toBeVisible()
  expect(resumeBody).toEqual({ conversation_id: 13, confirmed: false })
})

test('知识预览在编辑后失效，入库确认不使用旧块数', async ({ page }) => {
  await page.route('**/api/kb/overview', route => route.fulfill({ json: {
    chunks: { total: 3, pending: 0, done: 3, key_clause: 1 }, db_error: null, consistent: true,
    milvus: { online: true, count: 3, collection: 'knowledge' }, staging: null,
    content_types: [{ key: 'faq', desc: '问答知识' }], sources: [], jobs: [], recent: [],
  } }))
  await page.route('**/api/kb/preview', route => route.fulfill({ json: {
    source: '手工录入', total: 7, duplicates: 0, key_clause: 0, dedup_known: true,
    features: { sections: 1, overlap: false, table_split: false }, chunks: [],
  } }))
  await page.goto('/kb')
  await page.getByRole('textbox', { name: '知识正文' }).fill('旧正文')
  await page.getByRole('button', { name: '切块预览（不写库）' }).click()
  await expect(page.getByText('共 7 块 / 1 节')).toBeVisible()
  await page.getByRole('textbox', { name: '知识正文' }).fill('新正文')
  await expect(page.getByText('共 7 块 / 1 节')).toHaveCount(0)
  page.once('dialog', async dialog => { expect(dialog.message()).not.toContain('7 块'); await dialog.dismiss() })
  await page.getByRole('button', { name: '录入入库', exact: true }).click()
  await page.screenshot({ path: 'test-results/knowledge-desktop.png', fullPage: true })
})
