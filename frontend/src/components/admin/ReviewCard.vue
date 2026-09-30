<!-- 审核缺口卡片：独立懒加载详情；区分无检索、零命中、有召回三种证据状态。 -->
<script setup lang="ts">
import { ref } from 'vue'
import { useResource } from '../../composables/useResource'
import type { ReviewDetail, ReviewItem } from '../../types/review'
import { score, time } from '../../utils/format'
const props = defineProps<{ item: ReviewItem; disabled: boolean }>()
const emit = defineEmits<{ approve: []; reject: [] }>()
const open = ref(false)
const { data, error, loading, load } = useResource<ReviewDetail>()
const sourceLabels: Record<string, string> = { retrieval_low_conf: '检索置信度低', self_check: '模型自评不足', user_feedback: '用户反馈没解决' }
async function toggle() {
  open.value = !open.value
  if (open.value && !data.value && !loading.value) await load(`/api/review/${props.item.id}`)
}
</script>
<template><article class="card" :class="{ open }"><div class="row-main" @click="toggle">
  <span class="badge" :class="{ hot: item.occurrence_count >= 3 }" title="出现次数，越高越该优先补">×{{ item.occurrence_count }}</span>
  <button class="qtext" :aria-expanded="open" @click.stop="toggle">{{ item.normalized_question }}</button>
  <div class="ans-preview">{{ item.ai_suggested_answer || '(无示例答案)' }}</div><span class="badge" :class="`st-${item.review_status}`">{{ item.review_status }}</span>
  <div v-if="item.review_status === '待审'" class="ops"><button class="btn ok" :disabled="disabled" @click.stop="emit('approve')">通过</button><button class="btn no" :disabled="disabled" @click.stop="emit('reject')">驳回</button></div>
</div><div v-if="open" class="detail"><div v-if="loading">加载详情…</div><div v-if="error" role="alert">详情加载失败：{{ error }} <button class="btn sm" @click="load(`/api/review/${item.id}`)">重试</button></div>
  <template v-if="data"><div class="hint">对着快照判断：知识库是真缺这块，还是有但没检到。归并原话 {{ data.raws.length }} 条↓</div>
    <div v-if="!data.raws.length" class="nosnap">暂无归并原话（飞轮跑批后回填）</div>
    <div v-for="(raw, index) in data.raws" :key="index" class="raw"><div class="head"><span class="src" :class="`src-${raw.source}`">{{ sourceLabels[raw.source] || raw.source }}</span><span class="when">{{ time(raw.created_at) }}</span></div><div class="q">「{{ raw.raw_question }}」</div>
      <div v-if="raw.retrieved_chunks == null" class="nosnap">该入口未走检索，无召回快照</div><div v-else-if="!raw.retrieved_chunks.length" class="nosnap">已检索，零命中——知识库确实没有相关内容</div>
      <div v-else class="snaps"><div v-for="(chunk, n) in raw.retrieved_chunks" :key="n" class="snap"><div class="sq">{{ chunk.question || '(无标题)' }}</div><div class="sa">{{ chunk.answer }}</div>
        <div class="meter"><span>精排分 {{ score(chunk.rerank_score ?? 0) }}</span><div class="bar"><div class="fill" :style="{ width: `${Math.min(100, Math.max(0, (chunk.rerank_score || 0) * 100))}%` }" /></div><span>{{ chunk.section_path }}</span></div>
      </div></div>
    </div>
  </template>
</div></article></template>
<style scoped>
.card { background:var(--cream); border:4px solid var(--ink); box-shadow:var(--shadow); }.row-main { display:flex; align-items:center; gap:12px; padding:12px 16px; cursor:pointer; }.row-main:hover { background:var(--paper); }.qtext { font:inherit; color:inherit; text-align:left; background:transparent; border:0; cursor:pointer; font-weight:700; font-size:14.5px; flex:1; min-width:240px; }.ans-preview { color:var(--muted); font-size:12px; max-width:320px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }.badge { border:2.5px solid var(--ink); padding:2px 8px; font-size:12px; font-weight:700; background:var(--paper); white-space:nowrap; }.hot { background:var(--coral); color:#fff; }.st-待审 { background:var(--fur); }.st-通过,.btn.ok { background:var(--online); }.st-驳回,.btn.no { background:var(--error); color:#fff; }.ops { display:flex; gap:8px; }.detail { border-top:3px solid var(--ink); background:var(--paper); padding:14px 16px; }.hint { color:var(--muted); font-size:12px; margin:4px 0 10px; }.raw { border:3px solid var(--ink); background:var(--cream); margin-bottom:12px; padding:10px 12px; }.head { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:6px; }.src { border:2px solid var(--ink); padding:1px 7px; font-size:11.5px; font-weight:700; }.src-retrieval_low_conf { background:#ffd66e; }.src-self_check { background:#b7a6ff; }.src-user_feedback { background:#ff8095; }.when { color:var(--muted); font-size:11.5px; }.q { font-size:13.5px; margin:2px 0 8px; }.snaps { display:flex; flex-direction:column; gap:8px; }.snap { border:2px dashed var(--ink); padding:8px 10px; background:var(--paper); font-size:12.5px; }.sq { font-weight:700; }.sa { margin:3px 0; color:#4c4456; white-space:pre-wrap; }.meter { display:flex; align-items:center; gap:8px; font-size:11.5px; color:var(--muted); }.bar { height:8px; width:140px; border:2px solid var(--ink); background:var(--paper); }.fill { height:100%; background:var(--coral); }.nosnap { color:var(--muted); font-size:12px; border:2px dashed var(--muted); padding:6px 10px; }
@media(max-width:850px) { .row-main { flex-wrap:wrap; }.qtext { min-width:150px; }.ans-preview { width:100%; order:5; max-width:none; } }
</style>
