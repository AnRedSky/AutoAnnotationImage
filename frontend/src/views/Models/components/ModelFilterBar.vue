<script setup lang="ts">
/**
 * ModelFilterBar - 模型页筛选 + 批量操作
 *
 * v3.0.0 Phase I 拆分: 从 Models/index.vue 抽离, 包含:
 * - 任务类型下拉 (联动数据集)
 * - 数据集下拉 (联动任务类型)
 * - 关键词输入
 * - 批量操作按钮组 (激活 / 取消激活 / 删除 / 对比)
 *
 * 父组件只需绑定 v-model:taskType / v-model:datasetId / v-model:keyword,
 * 监听 change 事件触发 onFilterChange; 批量按钮触发 emit('batch-*')
 */
import { computed } from 'vue'
import { Delete, Search, VideoPlay, VideoPause } from '@element-plus/icons-vue'
import { TASK_TYPE_OPTIONS } from '@/utils/taskType'

const props = defineProps<{
  taskType: string
  datasetId: number | ''
  keyword: string
  datasets: Array<{ id: number; name: string; task_type: string }>
  selectedCount: number
  batchActivating: boolean
  batchDeleting: boolean
}>()

const emit = defineEmits<{
  (e: 'update:taskType', val: string): void
  (e: 'update:datasetId', val: number | ''): void
  (e: 'update:keyword', val: string): void
  (e: 'change:taskType'): void
  (e: 'change:filter'): void
  (e: 'batch-activate'): void
  (e: 'batch-deactivate'): void
  (e: 'batch-delete'): void
}>()

// 计算属性: 任务类型变化时, 数据集下拉只显示同任务类型的数据集
const filteredDatasets = computed(() => {
  if (!props.taskType) return props.datasets
  return props.datasets.filter((d) => d.task_type === props.taskType)
})

const onTaskTypeChange = (val: string) => {
  emit('update:taskType', val)
  emit('change:taskType')
}
const onDatasetChange = (val: number | '') => {
  emit('update:datasetId', val)
  emit('change:filter')
}
const onKeywordInput = (val: string) => {
  emit('update:keyword', val)
  emit('change:filter')
}
</script>

<template>
  <div class="filter-row">
    <!-- 任务类型下拉 (固定顺序, 复用 utils/taskType.ts) -->
    <el-select
      :model-value="taskType"
      clearable
      placeholder="任务类型"
      class="app-select filter-task-type"
      @update:model-value="onTaskTypeChange"
    >
      <el-option
        v-for="opt in TASK_TYPE_OPTIONS" :key="opt.value"
        :label="opt.label"
        :value="opt.value"
      />
    </el-select>
    <!-- 数据集下拉 (联动任务类型) -->
    <el-select
      :model-value="datasetId"
      clearable
      filterable
      placeholder="按数据集筛选"
      class="app-select"
      @update:model-value="onDatasetChange"
    >
      <el-option
        v-for="ds in filteredDatasets" :key="ds.id"
        :label="ds.name"
        :value="ds.id"
      />
    </el-select>
    <el-input
      :model-value="keyword"
      :prefix-icon="Search"
      clearable
      placeholder="搜索模型名 / 基础模型"
      class="filter-keyword"
      @update:model-value="onKeywordInput"
    />

    <div class="header-actions">
      <span class="selection-tip">
        已选 <strong>{{ selectedCount }}</strong> 个版本
      </span>
      <el-tooltip content="将选中的版本全部设为激活状态" placement="top">
        <el-button
          type="success"
          plain
          :icon="VideoPlay"
          :disabled="selectedCount === 0 || batchActivating"
          :loading="batchActivating"
          @click="emit('batch-activate')"
        >
          批量激活<span v-if="selectedCount > 0"> ({{ selectedCount }})</span>
        </el-button>
      </el-tooltip>
      <el-tooltip content="将选中的版本全部设为未激活状态" placement="top">
        <el-button
          type="info"
          plain
          :icon="VideoPause"
          :disabled="selectedCount === 0 || batchActivating"
          :loading="batchActivating"
          @click="emit('batch-deactivate')"
        >
          批量取消激活<span v-if="selectedCount > 0"> ({{ selectedCount }})</span>
        </el-button>
      </el-tooltip>
      <el-button
        type="danger"
        plain
        :icon="Delete"
        :disabled="selectedCount === 0 || batchDeleting"
        :loading="batchDeleting"
        @click="emit('batch-delete')"
      >
        批量删除<span v-if="selectedCount > 0"> ({{ selectedCount }})</span>
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  margin-bottom: 12px;
  background: #fff;
  border-radius: var(--radius-md);
  border: 1px solid var(--border-soft);
  flex-shrink: 0;
  flex-wrap: wrap;
}
.filter-keyword { width: 240px; flex-shrink: 0; }
.filter-task-type { width: 160px; flex-shrink: 0; }
.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.selection-tip {
  color: var(--text-secondary);
  font-size: 13px;
  white-space: nowrap;
}
.selection-tip strong {
  color: var(--brand-primary);
  font-weight: 600;
  font-size: 14px;
}
</style>
