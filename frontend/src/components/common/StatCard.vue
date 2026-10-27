<template>
  <div class="stat-card" :style="color ? { '--stat-color': color } : null">
    <span class="stat-card__label">{{ label }}</span>
    <div class="stat-card__value">
      <span>{{ value }}</span>
      <small v-if="unit">{{ unit }}</small>
    </div>
    <span v-if="tip" class="stat-card__tip">{{ tip }}</span>
    <el-icon v-if="icon" class="stat-card__icon" :size="16">
      <component :is="icon" />
    </el-icon>
  </div>
</template>

<script setup>
defineOptions({ name: 'StatCard' })

defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: '-' },
  unit: { type: String, default: '' },
  icon: { type: String, default: '' },
  // 传入后仅用于数值高亮，默认使用正文颜色
  color: { type: String, default: '' },
  tip: { type: String, default: '' },
})
</script>

<style scoped lang="scss">
/* 浅粉底指标块，对应参考页「知识总量 / 已发布」那一排 */
.stat-card {
  position: relative;
  @include flex($direction: column, $align: stretch, $gap: 4px);
  padding: 16px 18px 18px;
  border: 1px solid color-mix(in srgb, var(--el-color-primary) 7%, var(--app-border-color));
  border-radius: var(--app-radius);
  background-color: var(--app-tint-bg);
  transition:
    border-color 0.2s ease,
    background-color 0.2s ease;

  &:hover {
    border-color: color-mix(in srgb, var(--el-color-primary) 22%, var(--app-border-color));
    background-color: color-mix(in srgb, var(--el-color-primary) 4%, var(--app-tint-bg));
  }

  &__label {
    font-size: 13px;
    color: var(--app-text-secondary);
  }

  &__value {
    @include flex($align: baseline, $gap: 4px);
    font-size: 26px;
    font-weight: 700;
    line-height: 1.25;
    letter-spacing: -0.4px;
    color: var(--stat-color, var(--app-text-primary));

    small {
      font-size: 12px;
      font-weight: 500;
      letter-spacing: 0;
      color: var(--app-text-secondary);
    }
  }

  &__tip {
    @include ellipsis;
    font-size: 12px;
    color: var(--app-text-muted);
  }

  &__icon {
    position: absolute;
    top: 16px;
    right: 16px;
    color: color-mix(in srgb, var(--el-color-primary) 38%, transparent);
  }
}
</style>
