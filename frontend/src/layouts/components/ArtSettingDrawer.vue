<template>
  <el-drawer v-model="settingStore.settingVisible" title="界面设置" size="300px" :append-to-body="true">
    <div class="setting-drawer">
      <section class="setting-drawer__section">
        <h4 class="setting-drawer__label">主题模式</h4>
        <el-radio-group :model-value="settingStore.mode" size="small" @update:model-value="settingStore.setMode">
          <el-radio-button value="light">浅色</el-radio-button>
          <el-radio-button value="dark">深色</el-radio-button>
        </el-radio-group>
      </section>

      <section class="setting-drawer__section">
        <h4 class="setting-drawer__label">主题色</h4>
        <div class="setting-drawer__colors">
          <button
            v-for="preset in themeColorPresets"
            :key="preset.value"
            class="setting-drawer__color"
            :class="{ 'is-active': settingStore.primaryColor === preset.value }"
            :style="{ backgroundColor: preset.value }"
            :title="preset.name"
            type="button"
            @click="settingStore.setPrimaryColor(preset.value)"
          />
          <el-color-picker
            :model-value="settingStore.primaryColor"
            size="small"
            @update:model-value="(value) => value && settingStore.setPrimaryColor(value)"
          />
        </div>
      </section>

      <section class="setting-drawer__section">
        <h4 class="setting-drawer__label">界面显示</h4>
        <div class="setting-drawer__row">
          <span>显示页签（多标签时）</span>
          <el-switch v-model="settingStore.showTabs" size="small" />
        </div>
        <div class="setting-drawer__row">
          <span>显示面包屑</span>
          <el-switch v-model="settingStore.showBreadcrumb" size="small" />
        </div>
        <div class="setting-drawer__row">
          <span>菜单手风琴模式</span>
          <el-switch v-model="settingStore.uniqueOpened" size="small" />
        </div>
      </section>

      <el-button class="setting-drawer__reset" plain @click="settingStore.reset()">
        <el-icon><RefreshLeft /></el-icon>
        <span>恢复默认设置</span>
      </el-button>
    </div>
  </el-drawer>
</template>

<script setup>
import { themeColorPresets } from '@/config'
import { useSettingStore } from '@/store/modules/setting'

defineOptions({ name: 'ArtSettingDrawer' })

const settingStore = useSettingStore()
</script>

<style scoped lang="scss">
.setting-drawer {
  @include flex($direction: column, $align: stretch, $gap: 24px);

  &__section {
    @include flex($direction: column, $align: stretch, $gap: 12px);
  }

  &__label {
    font-size: 13px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__colors {
    @include flex($gap: 10px);
    flex-wrap: wrap;
  }

  &__color {
    width: 24px;
    height: 24px;
    border: 2px solid transparent;
    border-radius: 8px;
    box-shadow: 0 0 0 1px var(--app-border-color);
    transition: transform 0.15s ease;

    &:hover {
      transform: scale(1.08);
    }

    &.is-active {
      border-color: #ffffff;
      box-shadow: 0 0 0 2px var(--el-color-primary);
    }
  }

  &__row {
    @include flex($justify: space-between);
    font-size: 13px;
    color: var(--app-text-regular);
  }

  &__reset {
    width: 100%;
  }
}
</style>
