<template>
  <div class="app-shell">
    <ArtHeaderBar />

    <main class="app-main">
      <div class="app-container">
        <div v-if="showToolbar" class="app-toolbar">
          <el-breadcrumb v-if="settingStore.showBreadcrumb" class="app-toolbar__crumb" separator="/">
            <el-breadcrumb-item v-for="item in breadcrumbs" :key="item.path">
              {{ item.title }}
            </el-breadcrumb-item>
          </el-breadcrumb>

          <ArtTabsBar v-if="settingStore.showTabs && tabsStore.visitedViews.length > 1" class="app-toolbar__tabs" />
        </div>

        <router-view v-slot="{ Component, route }">
          <transition name="fade-slide" mode="out-in">
            <!-- key 中加入 refreshKey，配合顶栏刷新按钮强制重挂载页面 -->
            <component :is="Component" :key="`${route.name}-${appStore.refreshKey}`" />
          </transition>
        </router-view>
        <!--
        <footer class="app-footer">
          <span>{{ appConfig.copyright }}</span>
          <span class="app-footer__divider">·</span>
          <span>v{{ appConfig.version }}</span>
        </footer>
        -->
      </div>
    </main>

    <ArtSettingDrawer />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { appConfig } from '@/config'
import { useAppStore } from '@/store/modules/app'
import { useSettingStore } from '@/store/modules/setting'
import { useTabsStore } from '@/store/modules/tabs'
import { buildBreadcrumb } from '@/utils/menu'

import ArtHeaderBar from './components/ArtHeaderBar.vue'
import ArtSettingDrawer from './components/ArtSettingDrawer.vue'
import ArtTabsBar from './components/ArtTabsBar.vue'

defineOptions({ name: 'AppLayout' })

const route = useRoute()
const appStore = useAppStore()
const settingStore = useSettingStore()
const tabsStore = useTabsStore()

const breadcrumbs = computed(() => buildBreadcrumb(route.matched))
const showToolbar = computed(
  () =>
    (settingStore.showBreadcrumb && breadcrumbs.value.length > 0) ||
    (settingStore.showTabs && tabsStore.visitedViews.length > 1),
)
</script>

<style scoped lang="scss">
.app-shell {
  min-height: 100%;
  background-color: var(--app-bg);
}

/* 顶栏固定，内容区让出高度 */
.app-main {
  padding-top: var(--app-header-height);
}

.app-container {
  @include flex($direction: column, $gap: 18px);
  width: 100%;
  max-width: var(--app-container-max);
  margin: 0 auto;
  padding: 18px 32px 28px;
}

.app-toolbar {
  @include flex($gap: 20px);
  min-height: 28px;
  padding: 0 2px;

  &__crumb {
    flex-shrink: 0;

    :deep(.el-breadcrumb__inner) {
      color: var(--app-text-secondary);
      font-weight: 400;
    }

    :deep(.el-breadcrumb__item:last-child .el-breadcrumb__inner) {
      color: var(--app-text-primary);
    }
  }

  &__tabs {
    min-width: 0;
  }
}

.app-footer {
  @include flex($justify: center, $gap: 6px);
  padding-top: 4px;
  font-size: 12px;
  color: var(--app-text-muted);
}

@media (max-width: 768px) {
  .app-container {
    padding: 14px 16px 24px;
  }

  .app-toolbar {
    flex-wrap: wrap;
    gap: 10px;
  }
}
</style>
