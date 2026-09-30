<!-- 后台统一导航：保持旧 URL 与模块分组，分类器子页显示第二层导航。 -->
<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
const route = useRoute()
const modules = [
  ['/admin', '后台首页'], ['/kb', '知识库录入'], ['/rag-eval', 'RAG 评估'], ['/review', '飞轮待审'],
  ['/observability', '观测与成本'], ['/topics', '主题分布'], ['/acceptance', '分类器验收'],
]
const children = [['/acceptance', '总览'], ['/acceptance/eval', '评测详情'], ['/acceptance/data', '数据产物'], ['/acceptance/errors', '错例复核']]
const acceptance = computed(() => route.path.startsWith('/acceptance'))
function active(path: string) { return route.path === path || route.path.startsWith(`${path}/`) }
</script>

<template><nav class="mh-nav" aria-label="后台模块"><div class="row"><span class="brand">后台管理</span>
  <RouterLink v-for="[path, label] in modules" :key="path" :to="path!" :class="{ on: active(path!) }">{{ label }}</RouterLink>
  <span class="grow" /><RouterLink to="/">聊天页 →</RouterLink></div>
  <div v-if="acceptance" class="sub"><RouterLink v-for="[path, label] in children" :key="path" :to="path!" :class="{ on: route.path === path }">{{ label }}</RouterLink></div>
</nav></template>

<style scoped>
.mh-nav { margin-top: 12px; }
.row { display:flex; flex-wrap:wrap; align-items:stretch; border:3px solid #2b2632; background:#fffaf0; box-shadow:3px 3px 0 #2b2632; }
.row a { display:flex; align-items:center; gap:6px; padding:7px 13px; font-size:13px; text-decoration:none; color:#2b2632; border-right:3px solid #2b2632; font-weight:700; }
.row a:last-child { border-right:0; }.row a.on, .sub a.on { background:#ff9f57; }
.brand { display:flex; align-items:center; padding:7px 12px; font-size:12px; background:#2b2632; color:#fff6e6; border-right:3px solid #2b2632; letter-spacing:1px; }
.grow { flex:1; border-right:3px solid #2b2632; }
.sub { display:flex; flex-wrap:wrap; border:3px solid #2b2632; border-top:0; background:#fff6e6; box-shadow:3px 3px 0 #2b2632; }
.sub a { padding:5px 12px; font-size:12px; text-decoration:none; color:#2b2632; border-right:3px solid #2b2632; }
@media(max-width:640px) { .grow { display:none; } }
</style>
