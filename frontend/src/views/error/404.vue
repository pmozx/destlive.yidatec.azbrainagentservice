<template>
  <div class="not-found">
    <div class="not-found__code">404</div>
    <h2 class="not-found__title">页面不存在</h2>
    <p class="not-found__desc">
      访问的地址未匹配到任何页面，请检查链接是否正确，或返回运行总览。
    </p>
    <div class="not-found__actions">
      <el-button type="primary" @click="router.push('/dashboard')">返回运行总览</el-button>
      <el-button plain @click="router.back()">返回上一页</el-button>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'

defineOptions({ name: 'NotFoundView' })

const router = useRouter()
</script>

<style scoped lang="scss">
.not-found {
  @include flex($justify: center, $align: center, $gap: 12px, $direction: column);
  min-height: 100vh;
  padding: 24px;
  text-align: center;
  background-color: var(--app-bg);

  &__code {
    font-size: 96px;
    font-weight: 700;
    line-height: 1;
    letter-spacing: 4px;
    background: var(--app-brand-gradient);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }

  &__title {
    font-size: 20px;
    font-weight: 600;
    color: var(--app-text-primary);
  }

  &__desc {
    max-width: 420px;
    font-size: 13px;
    line-height: 1.7;
    color: var(--app-text-secondary);
  }

  &__actions {
    @include flex($gap: 12px);
    margin-top: 8px;
  }
}
</style>
