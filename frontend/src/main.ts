/** 应用启动：全局样式与路由仅初始化一次。 */
import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import './styles/theme.css'

createApp(App).use(router).mount('#app')
