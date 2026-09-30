/** 已有真实报告展示回归：使用仓库产物，不启动 Milvus、模型或训练服务。 */
import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'

const readReport = (path: string) =>
  JSON.parse(readFileSync(new URL('../../../' + path, import.meta.url), 'utf8'))

test('完整分类评测与 RAG 报告保持可读且无运行时错误', async ({ page }) => {
  const problems: string[] = []
  page.on('pageerror', (error) => problems.push(error.message))
  const evaluation = readReport('data/ch10/reports/eval_report.json')
  const scan = readReport('data/ch10/reports/threshold_scan.json')
  const rag = readReport('data/ch04/reports/rag_eval.json')
  await page.route('**/api/jobs', (route) => route.fulfill({ json: { jobs: [] } }))
  await page.route('**/api/acceptance/eval', (route) =>
    route.fulfill({
      json: {
        eval: { ...evaluation, present: true },
        scan: { ...scan, present: true },
        threshold_in_use: 0.45,
        severity: {},
        classifier: { online: false, detail: '未启动' },
      },
    }),
  )
  await page.goto('/acceptance/eval')
  await expect(
    page.getByRole('heading', { name: '每类指标与容错红线', exact: false }),
  ).toBeVisible()
  await expect(page.locator('.mats .mat')).toHaveCount(evaluation.classes.length)
  await expect(page.locator('.scanrow')).toHaveCount(scan.scan.length)
  await expect(page.locator('.scanrow.win')).toContainText('0.45')
  await page.screenshot({ path: 'test-results/acceptance-real-report.png', fullPage: true })
  await page.route('**/api/rag-eval/overview', (route) =>
    route.fulfill({
      json: {
        ...rag,
        present: true,
        generation_done: true,
        best: { strategy: 'hybrid_rerank', mrr: rag.retrieval.hybrid_rerank.overall.mrr },
        job: {
          specs: [],
          artifacts: {
            json: { path: 'rag_eval.json', present: true },
            text: { path: 'rag_eval.txt', present: true },
          },
        },
      },
    }),
  )
  await page.route('**/api/rag-eval/faith-cases?**', (route) =>
    route.fulfill({ status: 503, json: { detail: '本测试未启动 MySQL' } }),
  )
  await page.goto('/rag-eval')
  await expect(page.getByText('最佳整体 MRR')).toBeVisible()
  await expect(page.getByRole('alert')).toContainText('本测试未启动 MySQL')
  await page.getByRole('button', { name: 'Recall@5', exact: true }).click()
  await expect(page.getByRole('img', { name: 'Recall@5四策略分桶对照图' })).toBeVisible()
  await page.getByRole('button', { name: '证据覆盖度', exact: true }).click()
  await expect(page.getByRole('img', { name: '证据覆盖度四策略分桶对照图' })).toBeVisible()
  await page.screenshot({ path: 'test-results/rag-real-report.png', fullPage: true })
  expect(problems).toEqual([])
})
