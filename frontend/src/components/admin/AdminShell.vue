<!-- 后台页面外壳：共享标题、导航、加载/错误提示，保留各页面的内容布局。 -->
<script setup lang="ts">
import AdminNav from './AdminNav.vue'
import '../../styles/admin.css'
defineProps<{ title: string; subtitle?: string; loading?: boolean; error?: string }>()
const emit = defineEmits<{ refresh: [] }>()
</script>
<template>
  <div class="admin-page">
    <div class="wrap">
      <div class="topbar">
        <h1>{{ title }}</h1>
        <span class="sub">{{ subtitle }}</span> <span class="spacer" /><slot
          name="actions"
        /><button class="btn" :disabled="loading" @click="emit('refresh')">
          {{ loading ? '加载中…' : '刷新' }}
        </button>
      </div>
      <AdminNav />
      <div v-if="error" class="miss" role="alert">
        读取失败：{{ error }} <button class="btn sm" @click="emit('refresh')">重试</button>
      </div>
      <slot />
    </div>
  </div>
</template>
