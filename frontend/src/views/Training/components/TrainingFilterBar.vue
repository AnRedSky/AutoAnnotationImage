<script setup lang="ts">
/**
 * TrainingFilterBar - 训练页筛选 + 操作
 *
 * v3.0.0 Phase J 拆分: 从 Training/index.vue 抽离
 * - 任务类型下拉 (固定顺序: 分类/检测/分割)
 * - 状态下拉 (PENDING/PROGRESS/...)
 * - 数据集下拉 (联动任务类型, 仅显示同 task_type 的数据集)
 * - 模型关键词输入
 * - 重置按钮 (仅在有任一筛选时显示)
 * - 新建训练任务 + 刷新 (右侧操作组)
 *
 * 父组件:
 *   <TrainingFilterBar
 *     v-model:task-type="taskTypeFilter"
 *     v-model:state="stateFilter"
 *     v-model:dataset-id="datasetIdFilter"
 *     v-model:keyword="modelKeywordFilter"
 *     :datasets="DATASET_OPTIONS"
 *     @change:filter="..."
 *     @change:keyword="(v) => onModelKeywordChange(v)"
 *     @reset="resetFilters"
 *     @create="openCreateDialog"
 *     @refresh="loadJobs"
 *   />
 */
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { TASK_TYPE_OPTIONS } from '@/utils/taskType'

defineProps<{
  taskType: string
  state: string
  datasetId: number | null
  keyword: string
  datasets: any[]
  stateOptions: Array<{ value: string; label: string }>
}>()

const emit = defineEmits<{
  (e: 'update:taskType', val: string): void
  (e: 'update:state', val: string): void
  (e: 'update:datasetId', val: number | null): void
  (e: 'update:keyword', val: string): void
  (e: 'change:filter'): void
  (e: 'change:keyword', val: string): void
  (e: 'reset'): void
  (e: 'create'): void
  (e: 'refresh'): void
}>()

// 联动筛选: 任务类型变了, 数据集下拉只显示同 task_type 的数据集
const filterableDatasets = (datasets: any[], taskType: string) => {
  if (!taskType) return datasets
  return datasets.filter((d) => (d.task_type || 'classification') === taskType)
}
</script>

<template>
  <div class="filter-row">
    <!-- 任务类型下拉 -->
    <el-select
      :model-value="taskType"
      placeholder="任务类型"
      class="app-select app-select--narrow"
      clearable
      @update:model-value="(v: string) => { emit('update:taskType', v); emit('change:filter') }"
    >
      <el-option
        v-for="opt in TASK_TYPE_OPTIONS" :key="opt.value"
        :label="opt.label" :value="opt.value"
      />
    </el-select>
    <!-- 状态下拉 -->
    <el-select
      :model-value="state"
      placeholder="状态"
      class="app-select app-select--narrow"
      clearable
      @update:model-value="(v: string) => { emit('update:state', v); emit('change:filter') }"
    >
      <el-option
        v-for="o in stateOptions.filter((o) => o.value)" :key="o.value"
        :label="o.label" :value="o.value"
      />
    </el-select>
    <!-- 数据集下拉 -->
    <el-select
      :model-value="datasetId"
      placeholder="数据集"
      class="app-select"
      clearable
      filterable
      @update:model-value="(v: number | null) => { emit('update:datasetId', v); emit('change:filter') }"
    >
      <el-option
        v-for="d in filterableDatasets(datasets, taskType)" :key="d.id"
        :label="d.name" :value="d.id"
      />
    </el-select>
    <!-- 模型关键词 -->
    <el-input
      :model-value="keyword"
      placeholder="模型名 / 基础模型"
      class="filter-keyword"
      clearable
      :prefix-icon="Search"
      @update:model-value="(v: string) => { emit('update:keyword', v); emit('change:keyword', v) }"
    />
    <!-- 重置 -->
    <el-button
      v-if="state || datasetId != null || keyword || taskType"
      text
      :icon="Refresh"
      @click="emit('reset')"
    >
      重置
    </el-button>
    <div class="header-actions">
      <el-button type="primary" :icon="Plus" @click="emit('create')">新建训练任务</el-button>
      <el-button :icon="Refresh" @click="emit('refresh')">刷新</el-button>
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
.filter-keyword { width: 220px; flex-shrink: 0; }
.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
  flex-wrap: wrap;
  justify-content: flex-end;
}
</style>
