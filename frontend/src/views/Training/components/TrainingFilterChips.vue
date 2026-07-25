<script setup lang="ts">
/**
 * TrainingFilterChips - 训练页筛选状态条 + 批量操作工具条
 *
 * v3.0.0 Phase J 拆分: 从 Training/index.vue 抽离
 * - 筛选状态条: 当有任一筛选生效时, 显示当前命中条件 (任务类型/状态/数据集/关键词 chip)
 * - 批量操作工具条: 选中行时出现 (已选 N + 全选切换 + 批量删除 + 清空选择)
 *
 * 父组件:
 *   <TrainingFilterChips
 *     :task-type="taskTypeFilter" :state="stateFilter" :dataset-id="datasetIdFilter"
 *     :keyword="modelKeywordFilter" :total="total"
 *     :task-type-options="TASK_TYPE_OPTIONS" :state-options="STATE_OPTIONS"
 *     :selected-count="selectedJobIds.length" :all-selected="allOnPageSelected"
 *     :action-pending-count="Object.keys(actionPending).length"
 *     :dataset-name="datasetNameOf"
 *     @clear:task-type="..." @clear:state="..." @clear:dataset="..." @clear:keyword="..."
 *     @batch-delete="..." @toggle-select-all="..." @clear-selection="..."
 *   />
 */
import { Delete } from '@element-plus/icons-vue'

defineProps<{
  taskType: string
  state: string
  datasetId: number | null
  keyword: string
  total: number
  taskTypeOptions: Array<{ value: string; label: string }>
  stateOptions: Array<{ value: string; label: string }>
  selectedCount: number
  allSelected: boolean
  actionPendingCount: number
  datasetName: (id: number) => string
}>()

const emit = defineEmits<{
  (e: 'clear:taskType'): void
  (e: 'clear:state'): void
  (e: 'clear:dataset'): void
  (e: 'clear:keyword'): void
  (e: 'batch-delete'): void
  (e: 'toggle-select-all'): void
  (e: 'clear-selection'): void
}>()

const hasAnyFilter = (props: any) =>
  props.taskType || props.state || props.datasetId != null || props.keyword
</script>

<template>
  <!-- 筛选状态条 -->
  <div v-if="hasAnyFilter($props)" class="filter-chips">
    <span class="chips-label">当前筛选:</span>
    <el-tag v-if="taskType" type="info" effect="plain" closable @close="emit('clear:taskType')">
      任务类型: {{ taskTypeOptions.find((o) => o.value === taskType)?.label || taskType }}
    </el-tag>
    <el-tag v-if="state" type="info" effect="plain" closable @close="emit('clear:state')">
      状态: {{ stateOptions.find((o) => o.value === state)?.label || state }}
    </el-tag>
    <el-tag v-if="datasetId != null" type="info" effect="plain" closable @close="emit('clear:dataset')">
      数据集: {{ datasetId != null ? datasetName(datasetId) : '' }}
    </el-tag>
    <el-tag v-if="keyword" type="info" effect="plain" closable @close="emit('clear:keyword')">
      关键词: {{ keyword }}
    </el-tag>
    <span class="chips-count">
      共 <strong>{{ total }}</strong> 条命中
    </span>
  </div>

  <!-- 批量操作工具条 -->
  <div v-if="selectedCount > 0" class="batch-toolbar">
    <el-tag type="warning" effect="dark" size="default">
      已选 {{ selectedCount }} 个任务
    </el-tag>
    <el-button size="small" @click="emit('toggle-select-all')">
      {{ allSelected ? '取消全选' : '全选当前页' }}
    </el-button>
    <el-button
      type="danger" size="small" :icon="Delete"
      :loading="actionPendingCount > 0"
      @click="emit('batch-delete')"
    >批量删除</el-button>
    <el-button size="small" @click="emit('clear-selection')">清空选择</el-button>
  </div>
</template>

<style scoped>
/* 筛选 chip 状态条 */
.filter-chips {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 10px 14px;
  margin-bottom: 12px;
  background: linear-gradient(135deg, rgba(79, 124, 255, 0.04) 0%, rgba(110, 81, 233, 0.04) 100%);
  border: 1px solid rgba(79, 124, 255, 0.12);
  border-radius: var(--radius-md);
  flex-shrink: 0;
}
.chips-label {
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 500;
  margin-right: 4px;
}
.filter-chips :deep(.el-tag) {
  margin: 0;
  font-size: 12px;
  border-radius: var(--radius-sm);
}
.filter-chips :deep(.el-tag .el-tag__close) {
  background-color: transparent !important;
  color: var(--text-secondary);
}
.filter-chips :deep(.el-tag .el-tag__close:hover) {
  color: var(--brand-primary) !important;
  background-color: transparent !important;
}
.chips-count {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-secondary);
}
.chips-count strong {
  color: var(--brand-primary);
  font-weight: 600;
  font-size: 14px;
  margin: 0 2px;
}

/* 批量操作工具条 */
.batch-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  margin-bottom: 8px;
  background: linear-gradient(90deg, rgba(255, 169, 64, 0.08) 0%, rgba(255, 169, 64, 0.02) 100%);
  border: 1px solid rgba(255, 169, 64, 0.25);
  border-radius: var(--radius-md);
  flex-shrink: 0;
}
</style>
