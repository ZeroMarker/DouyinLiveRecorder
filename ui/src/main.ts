import { createApp } from 'vue'
import App from './App.vue'
import { initApi } from './api'
import './style.css'

// 后端未就绪也先挂载 UI（显示"连接失败"，App 内会重试直至就绪）
initApi().finally(() => createApp(App).mount('#app'))
