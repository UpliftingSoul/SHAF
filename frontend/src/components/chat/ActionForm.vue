<!-- 用户确认的写操作表单：工单手填、退款固定原因；失败保留输入，成功才禁用入口。 -->
<script setup lang="ts">
import { nextTick, onMounted, onScopeDispose, ref } from 'vue'
import { post } from '../../api/http'
import type { Action } from '../../types/chat'
const props = defineProps<{ action: Action; conversationId: number }>()
const emit = defineEmits<{ close: []; submitted: [ticketNo: string] }>()
const type = ref('')
const description = ref('')
const reason = ref('')
const busy = ref(false)
const error = ref('')
const dialog = ref<HTMLElement>()
const refund = props.action.type === 'refund_form'
const previousFocus = document.activeElement as HTMLElement | null
const controller = new AbortController()
function close() {
  if (!busy.value) emit('close')
}
async function submit() {
  if (busy.value) return
  if (refund && !reason.value) {
    error.value = '请选择退款原因'
    return
  }
  if (!refund && !type.value) {
    error.value = '请选择反馈类别'
    return
  }
  if (!refund && !description.value.trim()) {
    error.value = '请填写反馈描述'
    return
  }
  busy.value = true
  error.value = ''
  try {
    const result = await post<{ ticket_no: string }>(
      `/api/actions/create-${refund ? 'refund' : 'ticket'}`,
      {
        conversation_id: props.conversationId,
        ...(refund
          ? { order_id: props.action.draft?.order_id, reason: reason.value }
          : { ticket_type: type.value, description: description.value.trim() }),
      },
      controller.signal,
    )
    emit('submitted', result.ticket_no)
  } catch (cause) {
    if (!controller.signal.aborted) error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') close()
  if (event.key !== 'Tab' || !dialog.value) return
  const focusable = [
    ...dialog.value.querySelectorAll<HTMLElement>('button:not(:disabled), select, textarea, input'),
  ]
  const first = focusable[0],
    last = focusable.at(-1)
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  }
  if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}
onMounted(async () => {
  document.addEventListener('keydown', keydown)
  await nextTick()
  dialog.value?.querySelector<HTMLElement>(refund ? 'select' : 'textarea')?.focus()
})
onScopeDispose(() => {
  controller.abort()
  document.removeEventListener('keydown', keydown)
  previousFocus?.focus()
})
</script>

<template>
  <div class="modal-mask open" @click.self="close">
    <form
      ref="dialog"
      class="ticket-modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="action-title"
      @submit.prevent="submit"
    >
      <h3 id="action-title">{{ refund ? '提交退款工单' : '创建工单' }}</h3>
      <p class="sub">
        {{
          refund
            ? '确认订单与退款原因后提交,小喵会为您登记退款申请'
            : '小喵会把问题登记成工单跟进处理'
        }}
      </p>
      <template v-if="refund">
        <div class="ticket-field">
          <label for="refund-order">订单号</label
          ><input id="refund-order" :value="action.draft?.order_id" readonly />
        </div>
        <div class="ticket-field">
          <label for="refund-reason">退款原因 <span class="req">*</span></label>
          <select id="refund-reason" v-model="reason" :disabled="busy">
            <option disabled value="">请选择退款原因…</option>
            <option
              v-for="value in ['七天无理由', '质量问题', '发错货', '不想要了', '其他']"
              :key="value"
            >
              {{ value }}
            </option>
          </select>
        </div>
      </template>
      <template v-else>
        <div class="ticket-field">
          <label for="ticket-type">反馈类别 <span class="req">*</span></label>
          <select id="ticket-type" v-model="type" :disabled="busy">
            <option disabled value="">请选择反馈类别…</option>
            <option v-for="value in ['售后', '投诉', '咨询']" :key="value">{{ value }}</option>
          </select>
        </div>
        <div class="ticket-field">
          <label for="ticket-description">反馈描述 <span class="req">*</span></label>
          <textarea
            id="ticket-description"
            v-model="description"
            :disabled="busy"
            placeholder="请描述您遇到的问题…"
          />
        </div>
      </template>
      <div class="ticket-err" role="alert">{{ error }}</div>
      <div class="modal-actions">
        <button type="button" class="cancel" :disabled="busy" @click="close">取消</button>
        <button type="submit" class="submit" :disabled="busy">
          {{ busy ? '提交中…' : refund ? '提交退款' : '提交工单' }}
        </button>
      </div>
    </form>
  </div>
</template>
