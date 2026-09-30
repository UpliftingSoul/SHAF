<!-- 编造个案卡片：答案与完整证据快照、引用标记以及必须留说明的状态处置。 -->
<script setup lang="ts">
import { computed, nextTick, onScopeDispose, ref } from 'vue'
import { post } from '../../api/http'
import { buckets, strategies, type FaithCase, type FaithStatus } from '../../types/ragEval'
import { time } from '../../utils/format'
const props = defineProps<{ item: FaithCase }>()
const emit = defineEmits<{ changed: [] }>()
const target = ref<FaithStatus | null>(null)
const note = ref('')
const input = ref<HTMLInputElement>()
const busy = ref(false)
const error = ref('')
let controller: AbortController | undefined
const statusClass = { 未解决: 'open', 已解决: 'done', 无需解决: 'skip' }
const used = computed(() =>
  [...new Set([...props.item.answer.matchAll(/\[(\d+)\]/g)].map((match) => Number(match[1])))]
    .filter((n) => n >= 1 && n <= (props.item.citations?.length ?? 0))
    .sort((a, b) => a - b),
)
async function open(status: FaithStatus) {
  if (busy.value) return
  if (status === '未解决') {
    await submit(status)
    return
  }
  target.value = status
  note.value = ''
  error.value = ''
  await nextTick()
  input.value?.focus()
}
async function submit(status = target.value) {
  if (!status || busy.value) return
  error.value = ''
  if (status !== '未解决' && !note.value.trim()) {
    error.value = '请先写一句处置说明'
    input.value?.focus()
    return
  }
  busy.value = true
  controller = new AbortController()
  try {
    await post(
      `/api/rag-eval/faith-cases/${props.item.id}/status`,
      { status, resolution: status === '未解决' ? null : note.value.trim() },
      controller.signal,
    )
    target.value = null
    emit('changed')
  } catch (cause) {
    if (!controller.signal.aborted) error.value = '改状态失败：' + (cause as Error).message
  } finally {
    busy.value = false
  }
}
onScopeDispose(() => controller?.abort())
</script>
<template>
  <article class="fcase">
    <div class="fh">
      <span class="fid">{{ item.eval_id }}</span
      ><span class="fq grow">{{ item.query }}</span
      ><span v-if="item.reopened" class="st again">复发</span
      ><span class="st" :class="statusClass[item.status]">{{ item.status }}</span>
    </div>
    <div class="fmeta">
      {{ buckets.find((b) => b.key === item.bucket)?.label || item.bucket }} ·
      {{ strategies.find((s) => s.key === item.strategy)?.label || item.strategy }} · 被判
      {{ item.seen_count }} 次 · 最近 {{ time(item.last_seen_at)
      }}{{ item.judge_model ? ' · 裁判 ' + item.judge_model : '' }}
    </div>
    <div class="frow"><span class="k">裁判理由</span>{{ item.reason || '—' }}</div>
    <div v-if="item.resolution" class="frow">
      <span class="k">{{ item.status === '已解决' ? '怎么解决的' : '为什么不用改' }}</span
      >{{ item.resolution }}
    </div>
    <div class="frow">
      <span class="k">生成答案(原文)</span>
      <div class="ans">{{ item.answer }}</div>
    </div>
    <details class="cit">
      <summary v-if="item.citations?.length">
        证据原文 {{ item.citations.length }} 条（这一轮喂给模型的全部证据） ·
        {{
          used.length
            ? `答案引用了其中 ${used.length} 条：${used.map((n) => '[' + n + ']').join('')}`
            : '答案一条都没引'
        }}
      </summary>
      <summary v-else>证据原文 —— 这条个案入台账时还没记快照</summary>
      <div
        v-for="cite in item.citations"
        :key="cite.n"
        class="c1"
        :class="{ used: used.includes(cite.n) }"
      >
        <div>
          <span class="cn" :class="{ on: used.includes(cite.n) }">[{{ cite.n }}]</span>
          {{ cite.question }} <span v-if="used.includes(cite.n)" class="utag">答案引用</span>
        </div>
        <div class="ctext">{{ cite.answer }}</div>
        <div class="cp">
          {{ cite.section_path }}{{ cite.chunk_id ? '　·　chunk id ' + cite.chunk_id : '' }}
        </div>
      </div>
    </details>
    <div class="acts">
      <button :disabled="busy || item.status === '已解决'" @click="open('已解决')">已解决</button
      ><button :disabled="busy || item.status === '无需解决'" @click="open('无需解决')">
        无需解决</button
      ><button v-if="item.status !== '未解决'" :disabled="busy" @click="open('未解决')">
        退回未解决
      </button>
    </div>
    <form v-if="target" class="noteform" @submit.prevent="submit()">
      <input
        ref="input"
        v-model="note"
        maxlength="300"
        :disabled="busy"
        aria-label="处置说明"
        :placeholder="
          target === '已解决'
            ? '怎么解决的？请说明补了哪些知识或调整了什么'
            : '为什么不用改？请说明判断依据'
        "
      /><button :disabled="busy">{{ busy ? '提交中…' : '确定标为「' + target + '」' }}</button
      ><button type="button" class="ghost" :disabled="busy" @click="target = null">取消</button>
    </form>
    <div v-if="error" class="miss" role="alert">{{ error }}</div>
  </article>
</template>
<style scoped>
.fcase {
  border: 2px solid var(--ink);
  background: var(--cream);
  padding: 9px 11px;
  margin-top: 10px;
}
.fh {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.fid {
  font-size: 11px;
  border: 2px solid var(--ink);
  background: var(--fur);
  padding: 0 5px;
}
.fq {
  font-weight: 700;
  font-size: 13px;
}
.grow {
  flex: 1;
}
.fmeta,
.cp {
  font-size: 11px;
  color: var(--muted);
  margin-top: 3px;
}
.frow {
  font-size: 12.5px;
  line-height: 1.75;
  margin-top: 7px;
}
.k {
  display: block;
  font-size: 10.5px;
  color: var(--muted);
}
.ans {
  white-space: pre-wrap;
  border: 2px solid var(--ink);
  background: var(--paper);
  padding: 8px 10px;
  margin-top: 3px;
}
.st {
  font-size: 11px;
  border: 2px solid var(--ink);
  padding: 0 6px;
  background: var(--paper);
  white-space: nowrap;
}
.open {
  background: #ffe0e0;
}
.done {
  background: #e8fae9;
}
.skip {
  background: #eee;
  color: var(--muted);
}
.again {
  background: var(--fur);
  font-weight: 700;
}
.acts,
.noteform {
  display: flex;
  gap: 7px;
  margin-top: 9px;
  flex-wrap: wrap;
  align-items: center;
}
button {
  cursor: pointer;
  border: 2px solid var(--ink);
  background: var(--paper);
  padding: 3px 11px;
  font: inherit;
  font-size: 11.5px;
  box-shadow: 2px 2px 0 var(--ink);
}
button:disabled {
  opacity: 0.4;
  cursor: default;
  box-shadow: none;
}
.noteform input {
  flex: 1;
  min-width: min(260px, 100%);
  font: inherit;
  font-size: 12.5px;
  padding: 4px 8px;
  border: 2px solid var(--ink);
  background: var(--paper);
}
.noteform button {
  background: var(--fur);
}
.noteform .ghost {
  background: var(--paper);
  box-shadow: none;
}
.cit {
  border: 2px solid var(--ink);
  background: var(--paper);
  margin-top: 8px;
}
.cit summary {
  cursor: pointer;
  padding: 6px 10px;
  font-size: 11.5px;
  background: var(--cream);
  color: #6d6478;
}
.c1 {
  border-top: 2px solid var(--ink);
  padding: 8px 11px;
  font-size: 12.5px;
  line-height: 1.75;
}
.c1.used {
  background: #fffaf0;
}
.cn {
  font-weight: 700;
  border: 2px solid var(--ink);
  padding: 0 4px;
  background: var(--paper);
  color: var(--muted);
}
.cn.on,
.utag {
  background: var(--fur);
  color: var(--ink);
}
.utag {
  font-size: 10.5px;
  border: 2px solid var(--ink);
  padding: 0 5px;
  margin-left: 7px;
}
.ctext {
  margin-top: 4px;
  white-space: pre-wrap;
}
</style>
