<!-- 飞轮待审页：筛选、查看召回现场、核准答案写回与驳回，保留失败后的编辑内容。 -->
<script setup lang="ts">
import { nextTick, onMounted, onScopeDispose, ref, watch } from 'vue'
import AdminShell from '../components/admin/AdminShell.vue'
import ReviewCard from '../components/admin/ReviewCard.vue'
import { useResource } from '../composables/useResource'
import { post, query } from '../api/http'
import type { ReviewItem } from '../types/review'

const status = ref('待审')
const { data, error, loading, load } = useResource<{ items: ReviewItem[] }>()
const approving = ref<ReviewItem | null>(null),
  answer = ref(''),
  notice = ref(''),
  modalError = ref('')
const pending = ref(false)
const answerInput = ref<HTMLTextAreaElement>()
const controller = new AbortController()
function refresh() {
  return load(`/api/review/queue${query({ status: status.value || null })}`)
}
async function open(item: ReviewItem) {
  approving.value = item
  answer.value = item.ai_suggested_answer || ''
  modalError.value = ''
  await nextTick()
  answerInput.value?.focus()
}
function close() {
  if (!pending.value) approving.value = null
}
async function approve() {
  if (!approving.value || pending.value) return
  if (!answer.value.trim()) {
    modalError.value = '核准答案不能为空'
    return
  }
  const id = approving.value.id
  pending.value = true
  modalError.value = ''
  try {
    await post(
      `/api/review/${id}/approve`,
      { approved_answer: answer.value.trim() },
      controller.signal,
    )
    if (controller.signal.aborted) return
    approving.value = null
    notice.value = '已写回知识库，下次同类问题可直接检索命中 ✓'
    await refresh()
  } catch (cause) {
    if (!controller.signal.aborted) modalError.value = `写回失败：${(cause as Error).message}`
  } finally {
    pending.value = false
  }
}
async function reject(item: ReviewItem) {
  if (pending.value) return
  pending.value = true
  try {
    await post(`/api/review/${item.id}/reject`, undefined, controller.signal)
    notice.value = '已驳回'
    await refresh()
  } catch (cause) {
    if (!controller.signal.aborted) notice.value = `驳回失败：${(cause as Error).message}`
  } finally {
    pending.value = false
  }
}
function escape(event: KeyboardEvent) {
  if (event.key === 'Escape') close()
}
watch(status, refresh)
onMounted(() => {
  void refresh()
  document.addEventListener('keydown', escape)
})
onScopeDispose(() => {
  controller.abort()
  document.removeEventListener('keydown', escape)
})
</script>

<template>
  <AdminShell
    title="飞轮待审队列"
    subtitle="答不上的问题 → 标准化查重 → 人工审核 → 写回知识库"
    :loading="loading"
    :error="error"
    @refresh="refresh"
  >
    <template #actions
      ><div class="seg">
        <button
          v-for="value in ['待审', '通过', '驳回', '']"
          :key="value"
          :class="{ on: status === value }"
          :disabled="pending"
          @click="status = value"
        >
          {{ value || '全部' }}
        </button>
      </div></template
    >
    <div class="gates">
      审核前先过三道闸：<b>① 垃圾过滤</b>乱输入、测试胡打、不当言论 → 驳回；<b>② 时效</b
      >强时效问题补了就过期，不沉淀 → 驳回；<b>③ 频次</b>低频冷门问题不值得占知识库、耗人工 →
      驳回。剩下的才是真缺口，补上核准答案点通过。
    </div>
    <p v-if="notice" role="status">{{ notice }}</p>
    <div class="list">
      <div v-if="data && !data.items.length" class="empty">这个状态下暂时没有条目喵~</div>
      <ReviewCard
        v-for="item in data?.items"
        :key="`${status}-${item.id}`"
        :item="item"
        :disabled="pending"
        @approve="open(item)"
        @reject="reject(item)"
      />
    </div>
    <div v-if="approving" class="mask open" @click.self="close">
      <form
        class="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="approve-title"
        @submit.prevent="approve"
      >
        <h3 id="approve-title">通过并写回知识库</h3>
        <div class="mq">标准化问题：{{ approving.normalized_question }}</div>
        <textarea
          ref="answerInput"
          v-model="answer"
          aria-label="核准答案"
          :disabled="pending"
          placeholder="核准答案（预填 AI 示例答案，请人工把关后提交）"
        />
        <p v-if="modalError" class="err" role="alert">{{ modalError }}</p>
        <div class="foot">
          <button type="button" class="btn" :disabled="pending" @click="close">取消</button
          ><button class="btn ok" :disabled="pending">
            {{ pending ? '写回中…' : '确认通过' }}
          </button>
        </div>
      </form>
    </div>
  </AdminShell>
</template>

<style scoped>
:deep(.wrap) {
  max-width: 1080px;
}
.seg {
  display: flex;
  border: 3px solid var(--ink);
  background: var(--paper);
}
.seg button {
  border: 0;
  background: transparent;
  padding: 6px 12px;
  font: inherit;
  cursor: pointer;
  border-right: 3px solid var(--ink);
}
.seg button:last-child {
  border-right: 0;
}
.seg .on {
  background: var(--fur);
  font-weight: 700;
}
.gates {
  margin-top: 16px;
  background: var(--paper);
  border: 3px dashed var(--ink);
  padding: 10px 14px;
  font-size: 12.5px;
  line-height: 1.8;
}
.gates b {
  background: var(--fur);
  padding: 0 6px;
  border: 2px solid var(--ink);
  margin-right: 4px;
}
.list {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.empty {
  text-align: center;
  color: var(--muted);
  padding: 48px 0;
  font-size: 14px;
  background: var(--cream);
  border: 4px solid var(--ink);
  box-shadow: var(--shadow);
}
.mask {
  position: fixed;
  inset: 0;
  background: rgba(43, 38, 50, 0.45);
  display: flex;
  justify-content: center;
  align-items: center;
  z-index: 50;
}
.modal {
  width: min(560px, 92vw);
  background: var(--cream);
  border: 4px solid var(--ink);
  box-shadow: var(--shadow-lg);
  padding: 18px;
}
.modal h3 {
  margin: 0 0 4px;
  font-size: 15px;
}
.mq {
  font-size: 13px;
  color: #4c4456;
  margin-bottom: 10px;
}
.modal textarea {
  width: 100%;
  min-height: 130px;
  font: inherit;
  font-size: 13px;
  padding: 10px;
  border: 3px solid var(--ink);
  background: var(--paper);
  resize: vertical;
}
.foot {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 12px;
}
.btn.ok {
  background: var(--online);
}
.err {
  color: var(--error);
}
</style>
