/** 页面路由：保留旧系统所有业务 URL，后台子页按需加载。 */
import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: () => import('./views/ChatView.vue') },
    { path: '/kb', component: () => import('./views/KnowledgeView.vue') },
    { path: '/review', component: () => import('./views/ReviewView.vue') },
    { path: '/admin', component: () => import('./views/AdminView.vue') },
    { path: '/topics', component: () => import('./views/TopicsView.vue') },
    { path: '/topics/questions', component: () => import('./views/TopicQuestionsView.vue') },
    { path: '/observability', component: () => import('./views/ObservabilityView.vue') },
    { path: '/acceptance', component: () => import('./views/AcceptanceView.vue') },
    { path: '/acceptance/errors', component: () => import('./views/AcceptanceErrorsView.vue') },
    { path: '/acceptance/data', component: () => import('./views/AcceptanceDataView.vue') },
    { path: '/acceptance/eval', component: () => import('./views/AcceptanceEvalView.vue') },
    { path: '/rag-eval', component: () => import('./views/RagEvalView.vue') },
  ],
})
