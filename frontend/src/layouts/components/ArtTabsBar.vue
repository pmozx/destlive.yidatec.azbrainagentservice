<template>
  <div class="art-tabs">
    <el-scrollbar class="art-tabs__scroll">
      <div class="art-tabs__list">
        <button
          v-for="tab in tabsStore.visitedViews"
          :key="tab.path"
          class="art-tabs__item"
          :class="{ 'is-active': tab.path === activePath }"
          type="button"
          @click="goTo(tab)"
        >
          <span class="art-tabs__dot" />
          <span class="art-tabs__title">{{ tab.title }}</span>
          <el-icon v-if="!tab.affix" class="art-tabs__close" :size="12" @click.stop="closeTab(tab)">
            <Close />
          </el-icon>
        </button>
      </div>
    </el-scrollbar>

    <el-dropdown trigger="click" @command="handleCommand">
      <button class="art-tabs__more" type="button" title="页签操作">
        <el-icon :size="13"><ArrowDown /></el-icon>
      </button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item command="others">关闭其他</el-dropdown-item>
          <el-dropdown-item command="all">关闭全部</el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useTabsStore } from '@/store/modules/tabs'

defineOptions({ name: 'ArtTabsBar' })

const route = useRoute()
const router = useRouter()
const tabsStore = useTabsStore()

const activePath = computed(() => route.path)

function goTo(tab) {
  if (tab.path === route.path) return
  router.push(tab.fullPath || tab.path)
}

function fallbackRoute() {
  const last = tabsStore.visitedViews[tabsStore.visitedViews.length - 1]
  router.push(last ? last.fullPath || last.path : '/dashboard')
}

function closeTab(tab) {
  const isActive = tab.path === route.path
  tabsStore.removeView(tab.path)
  if (isActive) fallbackRoute()
}

function handleCommand(command) {
  if (command === 'others') {
    tabsStore.closeOthers(route.path)
    return
  }
  if (command === 'all') {
    tabsStore.closeAll()
    router.push('/dashboard')
  }
}
</script>

<style scoped lang="scss">
/* 页签退化为面包屑右侧的轻量胶囊，不再占用一条独立工具栏 */
.art-tabs {
  @include flex($gap: 6px);
  min-width: 0;

  &__scroll {
    flex: 0 1 auto;
    min-width: 0;
  }

  &__list {
    @include flex($gap: 4px);
    white-space: nowrap;
  }

  &__item {
    @include flex($gap: 6px, $inline: true);
    height: 26px;
    padding: 0 10px;
    border-radius: 999px;
    font-size: 12px;
    color: var(--app-text-secondary);
    transition:
      background-color 0.18s ease,
      color 0.18s ease;

    &:hover {
      background-color: color-mix(in srgb, var(--el-color-primary) 7%, transparent);
      color: var(--app-text-regular);
    }

    &.is-active {
      background-color: var(--app-card-bg);
      color: var(--el-color-primary);
      font-weight: 600;
      box-shadow: var(--app-shadow-soft);
    }
  }

  &__dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background-color: currentColor;
    opacity: 0.4;
  }

  &__item.is-active &__dot {
    opacity: 1;
  }

  &__close {
    margin-left: 1px;
    opacity: 0.5;
    transition: opacity 0.18s ease;

    &:hover {
      opacity: 1;
    }
  }

  &__more {
    @include flex-center;
    width: 26px;
    height: 26px;
    border-radius: 999px;
    color: var(--app-text-muted);

    &:hover {
      background-color: color-mix(in srgb, var(--el-color-primary) 8%, transparent);
      color: var(--el-color-primary);
    }
  }
}
</style>
