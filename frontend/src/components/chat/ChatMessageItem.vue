<!-- 单条消息：工具标记、引用、反馈、建议动作与两类暂停卡片。 -->
<script setup lang="ts">
import CatAvatar from './CatAvatar.vue'
import MessageContent from './MessageContent.vue'
import type { Action, ChatMessage } from '../../types/chat'
defineProps<{ message: ChatMessage; busy: boolean }>()
const emit = defineEmits<{
  feedback: [rating: 'up' | 'down']
  action: [action: Action]
  order: [orderId: string, resume: boolean]
  confirm: [confirmed: boolean]
}>()
const labels = { transfer_human: '转人工', create_ticket: '建工单', refund_form: '提交退款工单', select_order: '' }
</script>

<template>
  <div class="row" :class="message.role">
    <div v-if="message.role === 'bot'" class="avatar sm"><CatAvatar /></div>
    <div class="bubble" :class="{ error: message.error }">
      <template v-if="message.role === 'user' || message.error || message.plain">{{ message.text }}</template>
      <template v-else>
        <div v-if="message.tools.length" class="tool-badges">
          <div v-for="(tool, index) in message.tools" :key="index" class="tool-badge">🔧 调用了 {{ tool }}</div>
        </div>
        <MessageContent v-if="message.text" :text="message.text" :citations="message.citations" :decorate="!message.streaming" />
        <div v-else-if="message.streaming" class="typing"><span /><span /><span /></div>
        <template v-if="message.interrupt?.kind === 'select_order'">
          <div class="bubble-text">{{ message.interrupt.orders?.length ? '请选择您要处理的订单:' : '没有查到可选订单,请直接提供订单号。' }}</div>
          <div class="order-cards">
            <button v-for="order in message.interrupt.orders" :key="order.order_id" class="order-card"
              :class="{ picked: message.interrupt.picked === order.order_id }"
              :disabled="busy || message.interrupt.decided" @click="emit('order', order.order_id, true)">
              <div class="oc-top">订单 {{ order.order_id }}</div><div class="oc-mid">{{ order.product }}</div>
              <div class="oc-bot">{{ order.status }} · ¥{{ order.amount ?? '' }}</div>
            </button>
          </div>
        </template>
        <template v-if="message.interrupt?.kind === 'confirm_ticket'">
          <div class="bubble-text">请确认工单信息:</div>
          <div class="ticket-confirm" :class="{ decided: message.interrupt.decided }">
            <div class="tc-title">📋 工单预览</div>
            <div class="tc-row"><span class="tc-label">工单类型</span>{{ message.interrupt.preview?.ticket_type || '咨询' }}</div>
            <div class="tc-row"><span class="tc-label">问题描述</span>{{ message.interrupt.preview?.description }}</div>
            <div class="tc-btns">
              <button class="action-btn" :disabled="busy || message.interrupt.decided" @click="emit('confirm', true)">确认提交</button>
              <button class="action-btn tc-cancel" :disabled="busy || message.interrupt.decided" @click="emit('confirm', false)">取消</button>
            </div>
          </div>
        </template>
        <template v-if="!message.streaming && !message.interrupt">
          <template v-for="(action, index) in message.actions" :key="index">
            <div v-if="action.type === 'select_order'" class="order-cards">
              <div class="bubble-text">{{ action.orders?.length ? '请选择您要处理的订单:' : '没有查到可选订单,请直接提供订单号。' }}</div>
              <button v-for="order in action.orders" :key="order.order_id" class="order-card" :disabled="busy || action.used"
                @click="emit('order', order.order_id, false)">
                <div class="oc-top">订单 {{ order.order_id }}</div><div class="oc-mid">{{ order.product }}</div>
                <div class="oc-bot">{{ order.status }} · ¥{{ order.amount ?? '' }}</div>
              </button>
            </div>
          </template>
          <div v-if="message.actions.some(action => action.type !== 'select_order')" class="action-bar">
            <template v-for="(action, index) in message.actions" :key="index">
              <button v-if="action.type !== 'select_order'" class="action-btn" :disabled="busy || action.used"
                @click="emit('action', action)">{{ labels[action.type] }}</button>
            </template>
          </div>
          <div v-if="message.text && message.text !== '(无回复)'" class="feedback">
            <button v-for="rating in (['up', 'down'] as const)" :key="rating" class="fb-btn"
              :class="{ active: message.feedback === rating, dim: message.feedback && message.feedback !== rating }"
              :disabled="!!message.feedback || !message.conversationId" :aria-label="rating === 'up' ? '这条回答有帮助' : '这条回答没帮助'"
              @click="emit('feedback', rating)">
              <svg viewBox="0 0 24 24" fill="currentColor">
                <path v-if="rating === 'up'" d="M1 21h4V9H1v12zm22-11c0-1.1-.9-2-2-2h-6.31l.95-4.57.03-.32c0-.41-.17-.79-.44-1.06L14.17 1 7.59 7.59C7.22 7.95 7 8.45 7 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z" />
                <path v-else d="M15 3H6c-.83 0-1.54.5-1.84 1.22l-3.02 7.05c-.09.23-.14.47-.14.73v2c0 1.1.9 2 2 2h6.31l-.95 4.57-.03.32c0 .41.17.79.44 1.06L9.83 23l6.59-6.59c.36-.36.58-.86.58-1.41V5c0-1.1-.9-2-2-2zm4 0v12h4V3h-4z" />
              </svg>
            </button>
            <span v-if="message.feedback" class="fb-done">已反馈,谢谢~</span>
          </div>
        </template>
      </template>
    </div>
  </div>
</template>
