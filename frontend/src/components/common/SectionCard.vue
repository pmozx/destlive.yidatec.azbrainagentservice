<template>
  <section class="section-card">
    <header v-if="title || $slots.title || $slots.extra" class="section-card__header">
      <div class="section-card__heading">
        <slot name="title">
          <h3 class="section-card__title">{{ title }}</h3>
        </slot>
        <p v-if="subtitle" class="section-card__subtitle">{{ subtitle }}</p>
      </div>
      <div class="section-card__extra">
        <slot name="extra" />
      </div>
    </header>
    <div class="section-card__body" :class="{ 'is-flush': flush }">
      <slot />
    </div>
  </section>
</template>

<script setup>
defineOptions({ name: 'SectionCard' })

defineProps({
  title: { type: String, default: '' },
  subtitle: { type: String, default: '' },
  // 内容区去掉内边距（表格、代码块等贴边场景）
  flush: { type: Boolean, default: false },
})
</script>

<style scoped lang="scss">
/* 白色大圆角面板，与参考页的内容卡片一致；宽度跟随父容器，不按内容收缩 */
.section-card {
  @include flex($direction: column);
  @include panel;
  width: 100%;
  min-width: 0;
  overflow: hidden;

  &__header {
    @include flex($justify: space-between, $gap: 12px);
    padding: 22px 26px 14px;
  }

  &__heading {
    @include flex($align: baseline, $gap: 10px);
    min-width: 0;
  }

  &__title {
    font-size: 15px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__subtitle {
    font-size: 12px;
    color: var(--app-text-secondary);
  }

  &__extra {
    @include flex($gap: 8px);
    flex-shrink: 0;
  }

  &__body {
    flex: 1;
    min-width: 0;
    padding: 0 26px 24px;

    &.is-flush {
      padding: 0;
    }
  }
}
</style>
