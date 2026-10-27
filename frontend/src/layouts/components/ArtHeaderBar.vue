<template>
  <header class="art-nav">
    <div class="art-nav__inner">
      <div class="art-nav__brand" @click="goHome">
        <img class="art-nav__logo" src="@/assets/logo.png" alt="logo" />
        <div class="art-nav__brand-text">
          <strong>{{ appConfig.title }}</strong>
          <span>{{ appConfig.subtitle }}</span>
        </div>
      </div>

      <el-menu
        class="art-nav__menu"
        mode="horizontal"
        :default-active="activeMenu"
        :ellipsis="false"
        router
      >
        <template v-for="menu in menus" :key="menu.path">
          <el-sub-menu
            v-if="menu.children.length"
            :index="menu.path"
            :class="{ 'is-current': isSubMenuActive(menu) }"
          >
            <template #title>{{ menu.title }}</template>
            <el-menu-item v-for="child in menu.children" :key="child.path" :index="child.path">
              {{ child.title }}
            </el-menu-item>
          </el-sub-menu>
          <el-menu-item v-else :index="menu.path">{{ menu.title }}</el-menu-item>
        </template>
      </el-menu>

      <div class="art-nav__actions">
        <button
          v-if="headerBarConfig.showThemeToggle"
          class="art-nav__mode"
          type="button"
          :title="settingStore.isDark ? '切换浅色模式' : '切换深色模式'"
          @click="settingStore.toggleMode()"
        >
          <span>{{ settingStore.isDark ? 'Dark' : 'Light' }}</span>
          <span class="art-nav__switch" :class="{ 'is-on': settingStore.isDark }"><i /></span>
        </button>

        <button v-if="headerBarConfig.showRefresh" class="art-nav__icon-btn" type="button" title="刷新当前页" @click="refreshCurrent">
          <el-icon :size="17"><Refresh /></el-icon>
        </button>

        <button
          v-if="headerBarConfig.showFullscreen"
          class="art-nav__icon-btn"
          type="button"
          :title="isFullscreen ? '退出全屏' : '全屏显示'"
          @click="toggleFullscreen"
        >
          <el-icon :size="17"><Aim /></el-icon>
        </button>

        <button
          v-if="headerBarConfig.showSetting"
          class="art-nav__icon-btn"
          type="button"
          title="界面设置"
          @click="settingStore.settingVisible = true"
        >
          <el-icon :size="17"><Setting /></el-icon>
        </button>

        <el-dropdown v-if="headerBarConfig.showUserCenter" trigger="click" @command="handleUserCommand">
          <div class="art-nav__user">
            <el-avatar :size="26" class="art-nav__avatar">{{ userStore.avatarText }}</el-avatar>
            <span class="art-nav__username">{{ userStore.displayName }}</span>
            <el-icon :size="12"><ArrowDown /></el-icon>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="docs">
                <el-icon><Document /></el-icon>
                接口文档
              </el-dropdown-item>
              <el-dropdown-item command="yao">
                <el-icon><Files /></el-icon>
                YaoAPI 文档
              </el-dropdown-item>
              <el-dropdown-item command="logout" divided>
                <el-icon><SwitchButton /></el-icon>
                退出登录
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </div>
  </header>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { appConfig, headerBarConfig } from '@/config'
import { useAppStore } from '@/store/modules/app'
import { useSettingStore } from '@/store/modules/setting'
import { useTabsStore } from '@/store/modules/tabs'
import { useUserStore } from '@/store/modules/user'
import { buildMenus } from '@/utils/menu'

defineOptions({ name: 'ArtHeaderBar' })

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const settingStore = useSettingStore()
const tabsStore = useTabsStore()
const userStore = useUserStore()

const menus = computed(() => buildMenus(router.options.routes))
const activeMenu = computed(() => route.meta?.activeMenu || route.path)
const isFullscreen = ref(false)

/**
 * 横向菜单下 Element 只会给子项打 is-active，
 * 父级需要按当前路由前缀自行标注，才能与一级菜单保持同样的选中样式
 */
function isSubMenuActive(menu) {
  return activeMenu.value === menu.path || activeMenu.value.startsWith(`${menu.path}/`)
}

function goHome() {
  router.push('/dashboard')
}

function syncFullscreen() {
  isFullscreen.value = Boolean(document.fullscreenElement)
}

async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) {
      await document.exitFullscreen()
    } else {
      await document.documentElement.requestFullscreen()
    }
  } catch {
    ElMessage.warning('当前浏览器环境不支持全屏')
  }
}

function refreshCurrent() {
  appStore.refreshPage()
  ElMessage.success('页面已刷新')
}

async function handleUserCommand(command) {
  if (command === 'docs') {
    window.open(appConfig.swaggerUrl, '_blank')
    return
  }
  if (command === 'yao') {
    window.open(appConfig.yaoApiUrl, '_blank')
    return
  }
  if (command === 'logout') {
    try {
      await ElMessageBox.confirm('退出后本地会话信息将被清除，是否继续？', '退出登录', {
        type: 'warning',
        confirmButtonText: '确定退出',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
    userStore.logout()
    tabsStore.reset()
    router.push('/dashboard')
    ElMessage.success('已退出当前会话')
  }
}

onMounted(() => {
  syncFullscreen()
  document.addEventListener('fullscreenchange', syncFullscreen)
})

onBeforeUnmount(() => {
  document.removeEventListener('fullscreenchange', syncFullscreen)
})
</script>

<style scoped lang="scss">
/* 顶栏：深紫 → 品红渐变，参考 Service Brain 的导航条 */
.art-nav {
  position: fixed;
  top: 0;
  right: 0;
  left: 0;
  z-index: 100;
  height: var(--app-header-height);
  background-image: var(--app-nav-gradient);
  box-shadow: 0 6px 22px rgba(63, 12, 43, 0.16);

  &__inner {
    @include flex;
    height: 100%;
    max-width: var(--app-container-max);
    margin: 0 auto;
    padding: 0 32px;
  }

  &__brand {
    @include flex($gap: 10px);
    margin-right: 24px;
    flex-shrink: 0;
    cursor: pointer;
  }

  &__logo {
    width: 32px;
    height: 32px;
    border-radius: 9px;
  }

  &__brand-text {
    @include flex($direction: column, $align: flex-start);
    line-height: 1.15;
    white-space: nowrap;

    strong {
      font-size: 15px;
      font-weight: 700;
      letter-spacing: 0.2px;
      color: #ffffff;
    }

    span {
      font-size: 9.5px;
      letter-spacing: 0.22em;
      color: rgba(255, 255, 255, 0.55);
    }
  }

  /* 横向菜单：项占满顶栏高度，白色胶囊用伪元素绘制
     选择器统一下沉到 .art-nav 下，以免被 Element 的
     .el-menu--horizontal.el-menu / .el-menu--horizontal > .el-sub-menu .el-sub-menu__title 覆盖 */
  & &__menu {
    --el-menu-bg-color: transparent;
    --el-menu-hover-bg-color: transparent;
    --el-menu-text-color: var(--app-nav-text);
    --el-menu-active-color: var(--app-nav-text-active);
    --el-menu-horizontal-height: var(--app-header-height);
    @include flex;
    flex: 1;
    height: var(--app-header-height);
    min-width: 0;
    border-bottom: none;
    background-color: transparent;

    :deep(.el-menu-item),
    :deep(.el-sub-menu .el-sub-menu__title) {
      @include nav-pill;
      position: relative;
      isolation: isolate;
      @include flex;
      height: var(--app-header-height);
      margin: 0 2px;
      padding: 0 16px;
      border-bottom: none;
      border-radius: 0;
      line-height: normal;
      font-size: 14px;
      color: var(--app-nav-text);
      transition: color 0.18s ease;

      &:hover {
        color: #ffffff;

        &::before {
          background-color: rgba(255, 255, 255, 0.14);
        }
      }
    }

    /* 选中态：一级项由 Element 打 is-active，二级菜单的父项用自定义 is-current 标注 */
    :deep(.el-menu-item.is-active),
    :deep(.el-sub-menu.is-current > .el-sub-menu__title),
    :deep(.el-sub-menu.is-active > .el-sub-menu__title) {
      border-bottom: none;
      color: var(--app-nav-text-active);
      font-weight: 600;

      &::before {
        background-color: #ffffff;
      }
    }

    :deep(.el-sub-menu__icon-arrow) {
      color: inherit;
      margin-left: 4px;
    }
  }

  /* 右侧功能区 */
  &__actions {
    @include flex($gap: 6px);
    margin-left: 16px;
    flex-shrink: 0;
  }

  &__mode {
    @include flex($gap: 10px);
    height: 32px;
    margin-right: 6px;
    padding: 0 12px;
    border-radius: 999px;
    background-color: rgba(255, 255, 255, 0.14);
    color: #ffffff;
    font-size: 12px;
    letter-spacing: 0.3px;

    &:hover {
      background-color: rgba(255, 255, 255, 0.22);
    }
  }

  /* 自绘小开关，避免深色底上 Element 开关对比度不足 */
  &__switch {
    position: relative;
    width: 30px;
    height: 16px;
    border-radius: 999px;
    background-color: rgba(255, 255, 255, 0.28);
    transition: background-color 0.2s ease;

    i {
      position: absolute;
      top: 2px;
      left: 2px;
      width: 12px;
      height: 12px;
      border-radius: 50%;
      background-color: #ffffff;
      transition: transform 0.2s ease;
    }

    &.is-on {
      background-color: #ffffff;

      i {
        background-color: var(--app-brand);
        transform: translateX(14px);
      }
    }
  }

  &__icon-btn {
    @include flex-center;
    width: 32px;
    height: 32px;
    border-radius: 9px;
    color: var(--app-nav-text);
    transition:
      background-color 0.18s ease,
      color 0.18s ease;

    &:hover {
      background-color: rgba(255, 255, 255, 0.16);
      color: #ffffff;
    }
  }

  &__user {
    @include flex($gap: 8px);
    height: 36px;
    margin-left: 4px;
    padding: 0 12px 0 5px;
    border-radius: 999px;
    background-color: rgba(255, 255, 255, 0.14);
    color: #ffffff;
    cursor: pointer;
    outline: none;
    transition: background-color 0.18s ease;

    &:hover {
      background-color: rgba(255, 255, 255, 0.22);
    }
  }

  &__avatar {
    background-color: #ffffff;
    color: var(--app-brand);
    font-size: 12px;
    font-weight: 600;
  }

  &__username {
    @include ellipsis;
    max-width: 120px;
    font-size: 13px;
  }

  @media (max-width: 900px) {
    &__inner {
      padding: 0 16px;
    }

    &__brand-text span {
      display: none;
    }

    &__mode span:first-child {
      display: none;
    }
  }
}
</style>
