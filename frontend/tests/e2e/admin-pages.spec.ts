/** 后台路由回归：所有迁移页面可独立加载，验证报告缺失、旁路翻页和台账写回失败重试。 */
import { expect, test } from '@playwright/test'

const job = {
  name: 'eval-rag',
  title: '重跑 RAG 评估',
  cmd: 'make eval-rag',
  needs: 'Milvus',
  heavy: true,
  status: 'idle',
  returncode: null,
}
const missing = { present: false, hint: '测试产物尚未生成' }
const file = { path: 'data/test.jsonl', present: false }

test('后台首页、类目导航、观测和四张验收页', async ({ page }) => {
  const problems: string[] = []
  page.on('pageerror', (error) => problems.push(error.message))
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url())
    // Vite 源码 /src/api/http.ts 也含 /api/，仅拦截真正的后端 URL。
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const responses: Record<string, unknown> = {
      '/api/admin/overview': {
        modules: [
          {
            key: 'kb',
            title: '知识库',
            page: '/kb',
            lede: '入库与审核',
            status: 'attention',
            headline: '有待审知识',
            metrics: [{ label: '待审', value: 3 }],
            note: null,
          },
        ],
      },
      '/api/topics/distribution': {
        total: 3,
        latest: '2026-09-30T08:00:00',
        classes: [
          { label: '退款', count: 3, samples: ['我想退款'] },
          { label: '积分', count: 0, samples: [] },
        ],
      },
      '/api/topics/questions': {
        label: url.searchParams.get('label'),
        page: Number(url.searchParams.get('page')),
        pages: 2,
        total: 11,
        size: 10,
        items: [
          {
            question_id: Number(url.searchParams.get('page')),
            text: `第${url.searchParams.get('page')}页退款问题`,
            labels: ['退款'],
            source: 'user_feedback',
            occurrence_count: 2,
            review_status: '待审',
            classified_at: '2026-09-30T08:00:00',
            normalized: true,
            raw_question: '想退',
          },
        ],
      },
      '/api/observability/overview': {
        cost: { ...missing, status: 'missing', job },
        trend: { ...missing, status: 'missing', job },
        calibration: { ...missing, status: 'missing', job, in_use: 0.26, weights: {} },
      },
      '/api/acceptance/overview': {
        passed: 0,
        total: 9,
        all_pass: false,
        classifier: { online: false, detail: '未启动' },
        jobs: [job],
        blocks: [
          {
            key: 'eval',
            no: 5,
            title: '评测',
            page: '/acceptance/eval',
            status: 'missing',
            headline: '未生成',
            note: '需要评测',
            jobs: [],
          },
        ],
      },
      '/api/acceptance/eval': {
        eval: missing,
        scan: missing,
        threshold_in_use: null,
        severity: {},
        classifier: { online: false, detail: '未启动' },
      },
      '/api/acceptance/data': {
        lineage: [
          {
            ...file,
            file: 'corpus_labeled.jsonl',
            stage: '语料',
            desc: '标准问法',
            make: 'ch10-corpus',
          },
        ],
        dataset: {
          splits: Object.fromEntries(
            ['train', 'val', 'test'].map((key) => [
              key,
              { desc: key, size: 0, multi_label: 0, counts: { 退款: 0 }, file },
            ]),
          ),
          leaks: { train_val: 0, train_test: 0, val_test: 0 },
          clean: true,
        },
        sample_review: file,
        model: { files: [file], threshold: null, threshold_file: file, trio_ok: false },
        onnx: { files: [file], report: missing },
        topic_names: ['退款'],
      },
      '/api/acceptance/errors': { eval: missing, errors: [], kinds: {}, pairs: [] },
      '/api/jobs': { jobs: [job] },
      '/api/acceptance/classify': {
        text: '买大了想退',
        threshold: 0.45,
        labels: ['退款'],
        scores: [{ label: '退款', score: 0.3, hit: false }],
        fallback: true,
      },
    }
    return route.fulfill({
      json: responses[url.pathname] ?? { detail: '未匹配测试接口：' + url.pathname },
      status: responses[url.pathname] ? 200 : 404,
    })
  })
  await page.goto('/admin')
  await expect(page.getByRole('heading', { name: '后台管理', exact: true })).toBeVisible()
  await expect(page.getByText('有待审知识')).toBeVisible()
  await page.goto('/topics')
  await page.getByRole('link', { name: '退款', exact: true }).click()
  await expect(page.getByText('第1页退款问题')).toBeVisible()
  await page.getByRole('button', { name: '下一页 →' }).click()
  await expect(page.getByText('第2页退款问题')).toBeVisible()
  await expect(page).toHaveURL(/page=2/)
  for (const [path, heading] of [
    ['/observability', '观测与成本'],
    ['/acceptance', '分类器验收总览'],
    ['/acceptance/data', '分类器数据血缘'],
    ['/acceptance/errors', '分类器错例复核'],
    ['/acceptance/eval', '分类器评测验收'],
  ]) {
    await page.goto(path!)
    await expect(page.getByRole('heading', { name: heading!, exact: true })).toBeVisible()
    if (path !== '/acceptance')
      await expect(page.getByText('测试产物尚未生成').first()).toBeVisible()
  }
  await page.getByRole('button', { name: '试分类', exact: true }).click()
  await expect(page.getByText('命中标签：退款')).toBeVisible()
  await expect(page.getByText('17 类全部不过线', { exact: false })).toBeVisible()
  await page.screenshot({ path: 'test-results/acceptance-eval-desktop.png', fullPage: true })
  expect(problems).toEqual([])
})

test('RAG 缺值可显示、证据引用和处置说明失败重试', async ({ page }) => {
  const problems: string[] = []
  page.on('pageerror', (error) => problems.push(error.message))
  const item = {
    id: 8,
    eval_id: 'E01',
    bucket: 'E_multi',
    query: '多久退款',
    strategy: 'hybrid_rerank',
    answer: '预计七天[1]',
    reason: '证据未支持七天',
    citations: [
      { n: 1, question: '退款时间', answer: '原路退回', section_path: '退款/到账', chunk_id: '71' },
    ],
    judge_model: 'test-model',
    resolution: null as string | null,
    status: '未解决',
    seen_count: 2,
    reopened: true,
    first_seen_at: null,
    last_seen_at: null,
    resolved_at: null,
  }
  await page.route('**/api/rag-eval/overview', (route) =>
    route.fulfill({
      json: {
        present: true,
        meta: { n_samples: 1, kb_chunks: 2 },
        retrieval: {},
        evidence_coverage: {},
        generation_done: true,
        generation: {
          answer_coverage: {},
          faithfulness: {},
          faithfulness_cases: [],
          refusal: { rate: null, correct: 0, total: 0, cases: [] },
        },
        read_notes: {},
        best: { strategy: null, mrr: null },
        job: {
          specs: [job],
          artifacts: {
            json: { path: 'rag_eval.json', present: true },
            text: { path: 'rag_eval.txt', present: false },
          },
        },
      },
    }),
  )
  await page.route('**/api/rag-eval/faith-cases?**', (route) =>
    route.fulfill({
      json: {
        items: [item],
        total: 1,
        page: 1,
        pages: 1,
        size: 5,
        counts: {
          未解决: item.status === '未解决' ? 1 : 0,
          已解决: item.status === '已解决' ? 1 : 0,
          无需解决: 0,
        },
        hallucination: {
          evaluated: null,
          graded: null,
          absent: null,
          cases_judged: 1,
          cases_confirmed: 0,
          pending: 1,
          dismissed: 0,
          refusal_missed: 0,
          judged: 1,
          confirmed: 0,
          judged_rate: null,
          confirmed_rate: null,
          ledger: { total: 1, 未解决: 1, 已解决: 0, 无需解决: 0 },
        },
      },
    }),
  )
  let attempts = 0
  await page.route('**/api/rag-eval/faith-cases/8/status', (route) => {
    attempts++
    expect(route.request().postDataJSON()).toEqual({
      status: '已解决',
      resolution: '补充退款到账时间',
    })
    if (attempts === 1) return route.fulfill({ status: 503, json: { detail: '数据库暂不可用' } })
    item.status = '已解决'
    item.resolution = '补充退款到账时间'
    return route.fulfill({ json: item })
  })
  await page.goto('/rag-eval')
  await expect(page.getByRole('heading', { name: 'RAG 评估', exact: true })).toBeVisible()
  await expect(page.getByText('复发', { exact: true })).toBeVisible()
  await page.getByText('证据原文 1 条', { exact: false }).click()
  await expect(page.getByText('答案引用', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '已解决', exact: true }).click()
  await page.getByRole('button', { name: '确定标为「已解决」' }).click()
  await expect(page.getByRole('alert')).toContainText('请先写一句处置说明')
  await page.getByRole('textbox', { name: '处置说明' }).fill('补充退款到账时间')
  await page.getByRole('button', { name: '确定标为「已解决」' }).click()
  await expect(page.getByRole('alert')).toContainText('数据库暂不可用')
  await expect(page.getByRole('textbox', { name: '处置说明' })).toHaveValue('补充退款到账时间')
  await page.getByRole('button', { name: '确定标为「已解决」' }).click()
  await expect(page.getByText('怎么解决的', { exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/rag-eval-desktop.png', fullPage: true })
  expect(attempts).toBe(2)
  expect(problems).toEqual([])
})
