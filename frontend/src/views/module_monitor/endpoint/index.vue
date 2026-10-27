<template>
  <div class="page">
    <div class="page-header">
      <div>
        <h2 class="page-header__title">接口自检</h2>
        <p class="page-header__desc">逐个调用后端示例接口，快速确认服务可用性与响应结构</p>
      </div>
      <div class="page-header__actions">
        <el-tag v-if="passedCount" type="success" size="small" round>已成功 {{ passedCount }} 个</el-tag>
      </div>
    </div>

    <div class="endpoint-page">
      <SectionCard class="endpoint-page__aside" title="接口清单" subtitle="按控制器分组" flush>
        <div class="endpoint-groups">
          <div v-for="group in endpointCatalog" :key="group.group" class="endpoint-group">
            <h4 class="endpoint-group__title">{{ group.group }}</h4>
            <button
              v-for="item in group.items"
              :key="item.path"
              class="endpoint-item"
              :class="{ 'is-active': current?.path === item.path }"
              type="button"
              @click="runProbe(item)"
            >
              <span class="endpoint-item__method" :class="`is-${item.method.toLowerCase()}`">{{ item.method }}</span>
              <span class="endpoint-item__name">{{ item.name }}</span>
              <span class="endpoint-item__path">{{ item.path }}</span>
            </button>
          </div>
        </div>
      </SectionCard>

      <SectionCard
        class="endpoint-page__main"
        title="响应结果"
        :subtitle="current ? `${current.method} ${current.path}` : '请选择左侧接口'"
      >
        <template #extra>
          <el-button size="small" :loading="loading" :disabled="!current" @click="runProbe(current)">
            <el-icon><Refresh /></el-icon>
            <span>重新请求</span>
          </el-button>
        </template>

        <div v-if="result" class="endpoint-result">
          <div class="endpoint-result__meta">
            <el-tag :type="isSuccess ? 'success' : 'danger'" size="small">
              {{ result.status || 'ERR' }} {{ result.statusText }}
            </el-tag>
            <span class="text-secondary">耗时 {{ result.duration }} ms</span>
          </div>
          <JsonViewer :data="result.data" :title="current?.name || '响应报文'" max-height="440px" />
        </div>
        <el-empty v-else description="选择接口后查看响应报文" :image-size="90" />
      </SectionCard>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'

import { endpointCatalog, probeEndpoint } from '@/api/module_monitor/endpoint'
import JsonViewer from '@/components/common/JsonViewer.vue'
import SectionCard from '@/components/common/SectionCard.vue'

defineOptions({ name: 'MonitorEndpointView' })

const current = ref(null)
const result = ref(null)
const loading = ref(false)
const passedCount = ref(0)

const isSuccess = computed(() => result.value?.status >= 200 && result.value?.status < 300)

async function runProbe(item) {
  if (!item) return
  current.value = item
  loading.value = true
  try {
    result.value = await probeEndpoint(item.method, item.path, { absolute: item.absolute })
    if (isSuccess.value) passedCount.value += 1
  } catch (error) {
    result.value = {
      status: error.response?.status ?? 0,
      statusText: error.message || '请求失败',
      duration: 0,
      data: error.response?.data ?? { detail: error.message },
    }
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  const first = endpointCatalog[0]?.items[0]
  if (first) runProbe(first)
})
</script>

<style scoped lang="scss">
.endpoint-page {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: var(--app-gap);
  align-items: start;
}

.endpoint-groups {
  @include flex($direction: column, $align: stretch, $gap: 6px);
  padding: 4px 8px 12px;
  max-height: calc(100vh - 260px);
  overflow-y: auto;
}

.endpoint-group__title {
  padding: 10px 12px 6px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.4px;
  color: var(--app-text-secondary);
}

.endpoint-item {
  @include flex($gap: 8px);
  width: 100%;
  padding: 9px 12px;
  border-radius: 8px;
  text-align: left;
  color: var(--app-text-regular);
  transition: background-color 0.2s ease;

  &:hover {
    background-color: color-mix(in srgb, var(--el-color-primary) 7%, transparent);
  }

  &.is-active {
    background-color: color-mix(in srgb, var(--el-color-primary) 12%, transparent);
    color: var(--el-color-primary);
  }

  &__method {
    flex-shrink: 0;
    width: 42px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.4px;

    &.is-get {
      color: #0f9d78;
    }

    &.is-post {
      color: var(--el-color-primary);
    }

    &.is-put {
      color: #d98c00;
    }

    &.is-delete {
      color: #d64545;
    }
  }

  &__name {
    font-size: 13px;
    white-space: nowrap;
  }

  &__path {
    @include ellipsis;
    max-width: 130px;
    margin-left: auto;
    font-size: 11px;
    color: var(--app-text-secondary);
  }
}

.endpoint-result {
  @include flex($direction: column, $align: stretch, $gap: 12px);

  &__meta {
    @include flex($gap: 12px);
    font-size: 12px;
  }
}

@media (max-width: 1100px) {
  .endpoint-page {
    grid-template-columns: minmax(0, 1fr);
  }

  .endpoint-groups {
    max-height: 320px;
  }
}
</style>
