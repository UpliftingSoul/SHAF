<!-- 知识库录入页：六个原有业务区，使用后端统一切块、双写与人工审核接口。 -->
<script setup lang="ts">
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import AdminShell from '../components/admin/AdminShell.vue'
import JobRow from '../components/admin/JobRow.vue'
import ChunkPreview from '../components/admin/ChunkPreview.vue'
import RetrievalHits from '../components/admin/RetrievalHits.vue'
import { useResource } from '../composables/useResource'
import { post } from '../api/http'
import type { KnowledgeOverview, Preview, SearchResult, StagingData, StagingStatus } from '../types/kb'

const { data, error, loading, load } = useResource<KnowledgeOverview>()
const staging = useResource<StagingData>()
const text = ref(''), contentType = ref('faq'), vectorize = ref(true)
const preview = ref<Preview | null>(null), search = ref<SearchResult | null>(null)
const previewBusy = ref(false), ingestBusy = ref(false), vectorBusy = ref(false), searchBusy = ref(false)
const notice = ref(''), noticeError = ref(false)
const q = ref(''), strategy = ref('vector'), topK = ref(5)
const reviewing = ref<number | null>(null)
const controller = new AbortController()
let previewVersion = 0
let previewInput: { text: string; content_type: string } | null = null
const sample = '# 会员权益\n\n## 运费与包邮\n\n满 99 元包邮，未满收 10 元运费。偏远地区（新疆、西藏、内蒙）运费 20 元，不参与包邮。\n\n## 会员等级\n\n| 等级 | 年消费 | 折扣 | 生日礼 |\n|---|---|---|---|\n| 喵铜 | 0 元起 | 无 | 无 |\n| 喵银 | 1000 元起 | 95 折 | 优惠券 |\n| 喵金 | 5000 元起 | 9 折 | 猫罐头礼盒 |\n'
const stageLabels: Record<StagingStatus, string> = { kept: '待审（人工采纳才入库）', extracted: '已抽出待去重', discarded: '去重丢弃', approved: '已采纳入库', rejected: '已弃用' }
const stageOrder: StagingStatus[] = ['kept', 'extracted', 'discarded', 'approved', 'rejected']
const typeDescription = computed(() => data.value?.content_types.find(item => item.key === contentType.value)?.desc || '')
const sourceTotal = computed(() => data.value?.sources.reduce((sum, source) => sum + (source.chunks || 0), 0) || 0)
function jobs(names: string[]) { return data.value?.jobs.filter(job => names.includes(job.name)) || [] }
function refresh() { return load('/api/kb/overview') }
function inform(message: string, failed = false) { if (controller.signal.aborted) return; notice.value = message; noticeError.value = failed }
watch([text, contentType], () => { previewVersion++; preview.value = null; previewInput = null; previewBusy.value = false })

// 预览世代号既覆盖不同请求，也覆盖输入编辑，旧块数不会参与新正文的确认。
async function doPreview(file?: string) {
  const version = ++previewVersion
  const input = { text: text.value, content_type: contentType.value }
  previewBusy.value = true
  preview.value = null
  try {
    const result = await post<Preview>('/api/kb/preview', file ? { file } : input, controller.signal)
    if (version === previewVersion) { preview.value = result; previewInput = file ? null : input }
  } catch (cause) { if (version === previewVersion) inform(`预览失败：${(cause as Error).message}`, true) }
  finally { if (version === previewVersion) previewBusy.value = false }
}
async function ingest() {
  if (ingestBusy.value) return
  if (!text.value.trim()) { inform('先贴一段正文', true); return }
  const input = { text: text.value.trim(), content_type: contentType.value, vectorize: vectorize.value }
  const current = previewInput?.text.trim() === input.text && previewInput.content_type === input.content_type ? preview.value : null
  if (!window.confirm(`确认把这段正文录入知识库？${current ? `\n预览切出 ${current.total} 块，其中 ${current.duplicates} 块重复会跳过` : ''}\n${input.vectorize ? '入库后立刻向量化（要调嵌入上游）' : '只入库记 pending，稍后补向量'}`)) return
  ingestBusy.value = true
  try {
    const result = await post<{ inserted: number; skipped: number; vectorized: number | null }>('/api/kb/ingest', input, controller.signal)
    inform(`入库 ${result.inserted} 块，跳过重复 ${result.skipped} 块${result.vectorized === null ? '（未向量化）' : `，向量化 ${result.vectorized} 块`}`)
    await refresh()
    if (text.value.trim() === input.text && contentType.value === input.content_type) await doPreview()
  } catch (cause) { inform(`录入失败：${(cause as Error).message}`, true); await refresh() }
  finally { ingestBusy.value = false }
}
async function vectorizePending() {
  if (vectorBusy.value) return
  vectorBusy.value = true
  try {
    const result = await post<{ vectorized: number; chunk_stats: { pending: number } }>('/api/kb/vectorize', undefined, controller.signal)
    inform(`本次向量化 ${result.vectorized} 块，剩余 pending ${result.chunk_stats.pending}`)
    await refresh()
  } catch (cause) { inform(`向量化失败：${(cause as Error).message}`, true) }
  finally { vectorBusy.value = false }
}
async function doSearch(preset?: string) {
  if (searchBusy.value) return
  if (preset) q.value = preset
  if (!q.value.trim()) { inform('问一句话再检索', true); return }
  searchBusy.value = true
  search.value = null
  try { search.value = await post<SearchResult>('/api/kb/search', { q: q.value, strategy: strategy.value, top_k: Number(topK.value) }, controller.signal) }
  catch (cause) { inform(`检索失败：${(cause as Error).message}`, true) }
  finally { searchBusy.value = false }
}
async function review(kind: 'approve' | 'reject', id: number) {
  if (reviewing.value !== null) return
  reviewing.value = id
  try {
    await post(`/api/kb/staging/${kind}`, { ids: [id] }, controller.signal)
    inform(kind === 'approve' ? '已采纳入库' : '已弃用')
    await staging.load('/api/kb/staging')
    await refresh()
  } catch (cause) { inform(`${kind === 'approve' ? '采纳' : '弃用'}失败：${(cause as Error).message}`, true) }
  finally { reviewing.value = null }
}
onMounted(refresh)
onScopeDispose(() => { controller.abort(); previewVersion++ })
</script>

<template>
  <AdminShell title="知识库录入" subtitle="贴一段文档就能进库：切块 → 双写 MySQL 与 Milvus → 现场检索自测" :loading="loading" :error="error" @refresh="refresh">
    <div v-if="notice" class="notice" :class="{ err: noticeError }" role="status">{{ notice }}</div>
    <template v-if="data">
      <div class="gatebar"><div class="stat"><b>{{ data.chunks.total ?? '—' }}</b>知识块（MySQL）</div><div class="stat" :class="data.chunks.pending == null ? '' : data.chunks.pending ? 'fail' : 'pass'"><b>{{ data.chunks.pending ?? '—' }}</b>待向量化</div>
        <div class="stat"><b>{{ data.milvus.online ? data.milvus.count : '离线' }}</b>Milvus 条数</div><div class="stat"><b>{{ data.chunks.key_clause ?? '—' }}</b>关键条款</div>
        <div class="stat" :class="data.consistent === null ? '' : data.consistent ? 'pass' : 'fail'"><b>{{ data.consistent === null ? '读不到' : data.consistent ? '一致' : '对不上' }}</b>双写</div>
        <div v-if="data.db_error" class="stat fail"><b>MySQL</b>{{ data.db_error }}</div></div>
      <div class="tip">两条路：<b>手工录入</b>正文贴在这一页上，预览什么样就入库什么样；<b>离线建库</b>把 data/kb/ 的材料交给作业跑。先写 MySQL 记「待向量化」，再进 Milvus 回标「已向量化」，重跑捡 pending 补齐。</div>
      <section class="panel"><h2>① 手工录入 <span class="pill info">正文贴这里</span></h2><p class="lede">按标题层级切、超长递归切、句末重叠、大表格按行拆并复制表头。查重按「问法 + 正文」的指纹。</p>
        <div class="form"><div class="line"><label for="ctype">内容类型</label><select id="ctype" v-model="contentType" :disabled="ingestBusy"><option v-for="type in data.content_types" :key="type.key">{{ type.key }}</option></select><span class="ctdesc">{{ typeDescription }}</span><span class="spacer" />
          <button class="btn sm" :disabled="ingestBusy" @click="text = sample">填一段示例</button><button class="btn sm" :disabled="ingestBusy" @click="text = ''">清空</button></div>
          <textarea v-model="text" aria-label="知识正文" :disabled="ingestBusy" placeholder="贴 Markdown。带 # / ## 标题层级效果最好——政策手册没有天然问法时，questions 落章节标题、category 落上级路径。" />
          <div class="line"><button class="btn go" :disabled="previewBusy || ingestBusy" @click="doPreview()">{{ previewBusy ? '切块中…' : '切块预览（不写库）' }}</button><button class="btn" :disabled="ingestBusy" @click="ingest">{{ ingestBusy ? '录入中…' : '录入入库' }}</button>
            <label><input v-model="vectorize" type="checkbox" :disabled="ingestBusy" /> 入库后顺手向量化</label></div></div>
        <ChunkPreview v-if="preview" :preview="preview" />
      </section>
      <section class="panel"><h2>② 建库材料 <span class="pill info">{{ data.sources.length }} 份材料 / 共切 {{ sourceTotal }} 块</span></h2><p class="lede">材料清单就地切块（dry-run，不写库、不碰 Milvus、不调上游），点「看切块」逐块核对。</p>
        <div class="scroll-x"><table class="tbl"><thead><tr><th>文件</th><th>类型</th><th>字符</th><th>行</th><th>切出块数</th><th>关键条款</th><th>特性</th><th>操作</th></tr></thead><tbody>
          <tr v-for="source in data.sources" :key="source.file" :class="{ bad: !source.present }"><td>{{ source.path }}</td><td>{{ source.content_type }}</td><template v-if="source.present"><td class="num">{{ source.chars }}</td><td class="num">{{ source.lines }}</td><td class="num">{{ source.chunks }}</td><td class="num">{{ source.key_clause }}</td>
            <td><span v-if="source.features?.table_split" class="pill info">拆表</span><span v-if="source.features?.overlap" class="pill info">重叠</span><span class="pill missing">{{ source.features?.sections }} 节</span></td><td><button class="btn sm" :disabled="previewBusy" @click="doPreview(source.file)">看切块</button></td></template><td v-else colspan="6">文件不在</td></tr>
        </tbody></table></div><JobRow :jobs="jobs(['kb-preview', 'kb-build', 'kb-repatch'])" @finish="refresh" /><p class="needs">kb-build 有幂等守卫；改过 md 用 kb-repatch 原地改，改完记得向量化。</p>
      </section>
      <section class="panel"><h2>③ 对话挖知识 <span class="pill info">{{ data.staging ? `累计 ${data.staging.total} 条，最近批次 ${data.staging.latest_batch ?? '—'}` : '读不到' }}</span></h2><p class="lede">历史客服对话抽问答对，先落暂存表、整体去重，再经人工采纳进入知识库。</p>
        <div v-if="data.staging" class="kv"><div v-for="status in stageOrder" :key="status" class="box"><b>{{ data.staging.counts[status] ?? 0 }}</b>{{ stageLabels[status] }}</div><div class="box"><b>{{ data.staging.batches }}</b>批次数</div></div>
        <div v-else class="miss">MySQL 读不到，暂存表数看不了</div>
        <JobRow :jobs="jobs(['seed-conv', 'kb-mine'])" @finish="refresh" /><button class="btn sm" :disabled="staging.loading.value" @click="staging.load('/api/kb/staging')">看暂存表逐条</button>
        <div v-if="staging.error.value" class="miss">{{ staging.error.value }}</div>
        <template v-if="staging.data.value"><div v-if="!staging.data.value.stats.total" class="miss">暂存表是空的，先跑一次对话挖知识</div><template v-for="status in stageOrder" :key="status"><template v-if="staging.data.value.rows[status].length">
          <h3>{{ stageLabels[status] }}（{{ staging.data.value.rows[status].length }} 条）</h3><p v-if="status === 'kept'" class="lede">逐条看清楚再采纳，只对单笔订单成立、夹带订单号、答非所问的，都别放进知识库。</p>
          <div class="scroll-x"><table class="tbl"><thead><tr><th>批次</th><th>来源</th><th>问法</th><th>答案</th><th v-if="status === 'kept'">处理</th></tr></thead><tbody>
            <tr v-for="row in staging.data.value.rows[status]" :key="row.id"><td>{{ row.batch_no }}</td><td>{{ row.source_ref || '—' }}</td><td>{{ row.question }}</td><td><div class="ans">{{ row.answer }}</div></td><td v-if="status === 'kept'"><button class="btn sm" :disabled="reviewing !== null" @click="review('approve', row.id)">采纳</button><button class="btn sm" :disabled="reviewing !== null" @click="review('reject', row.id)">弃用</button></td></tr>
          </tbody></table></div>
        </template></template></template>
      </section>
      <section class="panel"><h2>④ 向量化与双写 <span class="pill" :class="data.consistent === null ? 'missing' : data.consistent ? 'pass' : 'fail'">{{ data.consistent === null ? '读不到，不下结论' : data.consistent ? '两边对得上' : '对不上，按下面补齐' }}</span></h2><p class="lede">MySQL 是原文权威源，Milvus 存检索索引。幂等靠 vectorize_status，pending 块可重跑补齐。</p>
        <div class="kv"><div class="box"><b>{{ data.chunks.pending ?? '—' }}</b>pending 待补</div><div class="box"><b>{{ data.chunks.done ?? '—' }}</b>done 已向量化</div><div class="box"><b>{{ data.milvus.online ? data.milvus.count : '离线' }}</b>Milvus 条数</div><div class="box"><b>{{ data.milvus.collection }}</b>集合</div></div>
        <div class="jobrow"><button class="btn go" :disabled="vectorBusy" @click="vectorizePending">{{ vectorBusy ? '向量化中…' : '向量化待补块' }}</button><span v-if="!data.milvus.online" class="needs">Milvus 离线：{{ data.milvus.detail }}</span></div>
        <JobRow :jobs="jobs(['kb-vectorize', 'kb-reset'])" @finish="refresh" /><p class="needs">清库会清空两表并 drop 集合，之后要重新建库。</p>
      </section>
      <section class="panel"><h2>⑤ 检索自测 <span class="pill info">换个说法问一句</span></h2><p class="lede">问题向量化后检索相似知识；切换路线对比 dense、关键词、混合及重排。</p>
        <form class="form" @submit.prevent="doSearch()"><div class="line"><input v-model="q" aria-label="检索问题" placeholder="邮费是多少" /><label for="strategy">路线</label><select id="strategy" v-model="strategy"><option value="vector">dense 向量单路（本章）</option><option value="bm25">BM25 关键词</option><option value="hybrid">混合召回</option><option value="hybrid_rerank">混合 + 重排</option></select><label for="top-k">Top-K</label><input id="top-k" v-model.number="topK" type="number" min="1" max="20" /><button class="btn go" :disabled="searchBusy">{{ searchBusy ? '检索中…' : '检索' }}</button></div></form>
        <div class="presets"><button v-for="preset in ['邮费是多少', '运费怎么算', '多久发货', '猫粮过期了能退吗']" :key="preset" class="btn sm" :disabled="searchBusy" @click="doSearch(preset)">{{ preset }}</button></div><RetrievalHits v-if="search" :result="search" />
      </section>
      <section class="panel"><h2>⑥ 最近入库</h2><p class="lede">按 id 倒序取最近 12 块，看原文和向量化状态。</p><div class="scroll-x"><table class="tbl"><thead><tr><th>ID</th><th>类型 / 章节</th><th>问法</th><th>正文摘要</th><th>状态</th></tr></thead><tbody>
        <tr v-for="row in data.recent" :key="row.id"><td>{{ row.id }}</td><td>{{ row.content_type }}<br />{{ row.section_path || row.category }}</td><td>{{ row.questions }}</td><td><div class="ans">{{ row.answer }}</div></td><td><span class="pill" :class="row.status === 'done' ? 'pass' : 'fail'">{{ row.status === 'done' ? '已向量化' : '待向量化' }}</span><span v-if="row.is_key_clause" class="pill fail">关键条款</span></td></tr>
      </tbody></table></div></section>
    </template>
  </AdminShell>
</template>

<style scoped>
.form { display:grid; gap:10px; }.line { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }.form label { font-size:12.5px; font-weight:700; }
select,input,textarea { font:inherit; font-size:13px; color:var(--ink); background:var(--paper); border:3px solid var(--ink); padding:6px 9px; }
input[type=checkbox] { width:auto; }input[type=number] { width:70px; }input:not([type]) { flex:1; min-width:220px; }textarea { width:100%; min-height:190px; line-height:1.75; resize:vertical; }
.ctdesc,.needs { color:#6d6478; font-size:11.5px; }.ans { white-space:pre-wrap; word-break:break-word; max-height:128px; overflow:auto; font-size:12px; line-height:1.7; }.kv,.presets { display:flex; gap:8px; flex-wrap:wrap; margin:10px 0; }.box { border:2px solid var(--ink); background:var(--paper); padding:4px 9px; font-size:11.5px; min-width:84px; }.box b { display:block; font-size:17px; line-height:1.4; }.notice { margin-top:14px; padding:10px; border:3px solid var(--ink); background:var(--paper); }.notice.err { color:var(--error); }
</style>
