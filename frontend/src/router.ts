/** 页面路由：保留旧系统所有业务 URL，后台子页按需加载。 */
import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('./views/ChatView.vue') },
    { path: '/kb', component: () => import('./views/KnowledgeView.vue') },
    { path: '/review', component: () => import('./views/ReviewView.vue') },
  ],
})
