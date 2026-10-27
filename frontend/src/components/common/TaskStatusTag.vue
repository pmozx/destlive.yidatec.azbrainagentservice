<template>
  <el-tag :type="meta.type" size="small" effect="light" round>
    <span class="status-dot" :class="`is-${status}`" />
    {{ meta.label }}
  </el-tag>
</template>

<script setup>
import { computed } from 'vue'

import { AgentTaskStatusMeta } from '@/enums'

defineOptions({ name: 'TaskStatusTag' })

const props = defineProps({
  status: { type: String, required: true },
})

const meta = computed(() => AgentTaskStatusMeta[props.status] || { label: props.status, type: 'info' })
</script>

<style scoped lang="scss">
.status-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 4px;
  border-radius: 50%;
  background-color: currentColor;
  vertical-align: middle;

  &.is-running {
    animation: pulse 1.2s ease-in-out infinite;
  }
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }

  50% {
    opacity: 0.25;
  }
}
</style>
