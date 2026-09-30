<!-- 聊天页：保持原 UI，视图只分发用户操作，网络与会话状态由 useChat 管理。 -->
<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { useChat } from '../composables/useChat'
import CatAvatar from '../components/chat/CatAvatar.vue'
import ChatMessageItem from '../components/chat/ChatMessageItem.vue'
import ActionForm from '../components/chat/ActionForm.vue'
import type { Action, ChatMessage } from '../types/chat'
import '../styles/chat.css'

const chat = useChat()
const { messages, conversations, conversationId, busy, historyError } = chat
const input = ref('')
const composer = ref<HTMLTextAreaElement>()
const scroller = ref<HTMLElement>()
const form = ref<{ action: Action; conversationId: number } | null>(null)
const suggestions = ['退换货政策是怎样的?', '怎么查我的物流进度?', '猫粮怎么选?', '会员有什么权益?']
function autoGrow() {
  if (!composer.value) return
  composer.value.style.height = 'auto'
  composer.value.style.height = `${Math.min(composer.value.scrollHeight, 128)}px`
}
async function send() {
  if (busy.value || !input.value.trim()) return
  const text = input.value
  input.value = ''
  await nextTick()
  autoGrow()
  await chat.send(text)
  composer.value?.focus()
}
function keydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    void send()
  }
}
function action(message: ChatMessage, value: Action) {
  if (busy.value || value.used) return
  if (value.type === 'transfer_human') chat.transfer(value)
  else if (message.conversationId)
    form.value = { action: value, conversationId: message.conversationId }
}
function submitted(ticketNo: string) {
  const current = form.value
  if (!current) return
  current.action.used = true
  if (conversationId.value === current.conversationId) {
    chat.addMessage(
      'bot',
      `${current.action.type === 'refund_form' ? '退款申请已提交:' : '工单已创建:'}${ticketNo}`,
      '',
      true,
    )
  }
  form.value = null
}
function pick(message: ChatMessage, orderId: string, resume: boolean) {
  if (resume) void chat.resume(message, { order_id: orderId })
  else {
    const source = message.actions.find((value) => value.type === 'select_order')
    if (source?.used || busy.value) return
    if (source) source.used = true
    void chat.send(`查一下订单 ${orderId}`)
  }
}
watch(
  messages,
  async () => {
    await nextTick()
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
  },
  { deep: true },
)
onMounted(() => {
  void chat.refreshConversations()
  composer.value?.focus()
})
</script>

<template>
  <div class="chat-page">
    <div class="app">
      <aside id="sidebar">
        <div class="sb-head">会话记录</div>
        <div id="conv-list">
          <div v-if="!conversations.length" class="sb-empty">还没有会话,发条消息开始吧~</div>
          <button
            v-for="item in conversations"
            :key="item.id"
            class="conv-item"
            :class="{ active: item.id === conversationId }"
            :disabled="busy"
            @click="chat.switchConversation(item.id)"
          >
            <div class="ci-top">
              #{{ item.id }} <span v-if="item.has_summary" class="ci-sum">已摘要</span>
            </div>
            <div class="ci-preview">{{ item.preview }}</div>
          </button>
        </div>
      </aside>
      <div class="main">
        <header>
          <div class="avatar lg"><CatAvatar /></div>
          <div class="head-meta">
            <span class="name">小喵 · 智能客服</span>
            <span class="status"><span class="dot-online" />ONLINE · 喵喵优选</span>
          </div>
          <div class="spacer" />
          <button id="new-chat" :disabled="busy || !!form" @click="chat.newConversation()">
            ＋ 新对话
          </button>
        </header>
        <div id="messages" ref="scroller">
          <p v-if="historyError" class="history-error" role="alert">
            历史加载失败：{{ historyError }}
            <button @click="conversationId && chat.switchConversation(conversationId)">重试</button>
          </p>
          <div v-if="!messages.length && !historyError && !busy" class="empty">
            <div class="mascot"><CatAvatar /></div>
            <h1>你好,我是小喵</h1>
            <p>喵喵优选的智能客服,商品、订单、售后都能问我~</p>
            <div class="chips">
              <button
                v-for="value in suggestions"
                :key="value"
                class="chip"
                :disabled="busy"
                @click="chat.send(value)"
              >
                {{ value }}
              </button>
            </div>
          </div>
          <p v-if="busy && !messages.length">加载会话中…</p>
          <ChatMessageItem
            v-for="message in messages"
            :key="message.id"
            :message="message"
            :busy="busy || !!form"
            @feedback="chat.feedback(message, $event)"
            @action="action(message, $event)"
            @order="(id, resume) => pick(message, id, resume)"
            @confirm="chat.resume(message, { confirmed: $event })"
          />
        </div>
        <footer>
          <div class="composer">
            <textarea
              id="input"
              ref="composer"
              v-model="input"
              rows="1"
              :disabled="busy || !!form"
              placeholder="输入消息,和小喵聊聊吧~(回车发送 / Shift+回车换行)"
              @input="autoGrow"
              @keydown="keydown"
            />
            <button id="send" aria-label="发送" :disabled="busy || !!form" @click="send">
              <svg viewBox="0 0 16 16" shape-rendering="crispEdges" fill="currentColor">
                <path
                  d="M2 7h8v2H2zM8 5h2v2H8zM8 9h2v2H8zM10 3h2v2h-2zM10 11h2v2h-2zM12 1h2v2h-2zM12 13h2v2h-2z"
                />
              </svg>
            </button>
          </div>
          <p class="foot-note">小喵是 AI 助手,涉及具体订单会为你转接人工核实</p>
        </footer>
      </div>
    </div>
    <ActionForm
      v-if="form"
      :action="form.action"
      :conversation-id="form.conversationId"
      @close="form = null"
      @submitted="submitted"
    />
  </div>
</template>
