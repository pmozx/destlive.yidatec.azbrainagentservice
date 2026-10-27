import { createApp } from 'vue'

import App from './App.vue'
import { initPlugins } from './plugins'
import pinia from './store'
import { useSettingStore } from './store/modules/setting'

const app = createApp(App)

initPlugins(app)

// 首屏恢复主题模式与主题色（index.html 中的内联脚本已先处理暗色，避免闪白）
useSettingStore(pinia).initTheme()

app.mount('#app')
