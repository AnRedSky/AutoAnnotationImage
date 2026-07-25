<script setup lang="ts">
/**
 * ModelTable - 模型列表表格
 *
 * v3.0.0 Phase I 拆分: 从 Models/index.vue 抽离, 包含:
 * - 表格 (多选 + 任务类型/状态/指标列 + 行操作)
 * - 行内: 激活/取消激活/详情/删除
 * - 表格固定表头 + 内部滚动 (v2.5.28 行高稳定性改造)
 *
 * 父组件:
 *   <ModelTable
 *     :data="pagedData"
 *     :loading="loading"
 *     :filter-keyword="filterKeyword"
 *     :filter-task-type="filterTaskType"
 *     :filter-dataset-id="filterDatasetId"
 *     :index-method="indexMethod"
 *     @selection-change="..."
 *     @activate="..."
 *     @deactivate="..."
 *     @detail="..."
 *     @delete="..."
 *   />
 */
import { Grid, CircleCheck } from '@element-plus/icons-vue'
import { getTaskTypeMeta } from '@/utils/taskType'

const props = defineProps<{
  data: any[]
  loading: boolean
  filterKeyword: string
  filterTaskType: string
  filterDatasetId: number | ''
  indexMethod: (idx: number) => number
}>()

const emit = defineEmits<{
  (e: 'selection-change', rows: any[]): void
  (e: 'activate', id: number): void
  (e: 'deactivate', id: number): void
  (e: 'detail', id: number): void
  (e: 'delete', row: any): void
}>()

// 数值格式化 helpers (从原 page 同步过来)
const pct = (v: any) => (v != null ? `${(Number(v) * 100).toFixed(2)}%` : '-')
const f1fmt = (v: any) => (v != null ? Number(v).toFixed(3) : '-')
</script>

<template>
  <div class="table-wrapper">
    <el-table
      v-loading="loading"
      :data="data"
      :row-key="(row: any) => row.id"
      stripe
      class="data-table"
      height="100%"
      style="width: 100%;"
      @selection-change="(rows: any[]) => emit('selection-change', rows)"
    >
      <template #empty>
        <div class="empty-state">
          <div class="empty-state__icon empty-state__icon--brand">
            <el-icon><Grid /></el-icon>
          </div>
          <div class="empty-state__title">
            {{ filterKeyword || filterTaskType || filterDatasetId !== ''
              ? '没有匹配的模型' : '还没有模型版本' }}
          </div>
          <div class="empty-state__desc">
            {{ (filterKeyword || filterTaskType || filterDatasetId !== '')
              ? '尝试调整筛选条件'
              : '到「训练任务」页选定数据集并启动训练, 完成后模型会自动出现在这里'
            }}
          </div>
        </div>
      </template>
      <el-table-column type="index" :index="indexMethod" label="#" width="42" />
      <el-table-column type="selection" width="40" />
      <el-table-column prop="name" label="模型名" min-width="200">
        <template #default="{ row }">
          <div class="model-name-cell">
            <div class="model-icon">
              <el-icon><Grid /></el-icon>
            </div>
            <span :class="{ 'is-active-name': row.is_active }">{{ row.name }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="base_model" label="基础模型" min-width="120" align="center">
        <template #default="{ row }">
          <el-tag size="small" type="info" effect="plain">{{ row.base_model }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="任务类型" width="120" align="center">
        <template #default="{ row }">
          <el-tag
            :type="getTaskTypeMeta(row.task_type || 'classification').type"
            effect="plain"
            size="small"
          >
            {{ getTaskTypeMeta(row.task_type || 'classification').label }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="is_active" label="状态" width="120" align="center">
        <template #default="{ row }">
          <el-tag v-if="row.is_active" type="success" effect="dark" size="small">
            <el-icon style="margin-right: 2px;"><CircleCheck /></el-icon>已激活
          </el-tag>
          <el-tag v-else effect="plain" size="small">未激活</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="accuracy" label="准确率" width="100" align="center">
        <template #default="{ row }">
          <span :class="['metric', 'metric--acc', { 'is-strong': Number(row.accuracy || 0) >= 0.8 }]">
            {{ pct(row.accuracy) }}
          </span>
        </template>
      </el-table-column>
      <el-table-column prop="precision" label="精确率" width="100" align="center">
        <template #default="{ row }">{{ pct(row.precision) }}</template>
      </el-table-column>
      <el-table-column prop="recall" label="召回率" width="100" align="center">
        <template #default="{ row }">{{ pct(row.recall) }}</template>
      </el-table-column>
      <el-table-column prop="f1_score" label="F1" width="80" align="center">
        <template #default="{ row }">{{ f1fmt(row.f1_score) }}</template>
      </el-table-column>
      <el-table-column prop="dataset_id" label="训练集" min-width="140" align="center">
        <template #default="{ row }">
          <el-tooltip v-if="row.dataset_name" :content="`数据集 ID: ${row.dataset_id}`" placement="top">
            <span class="ds-name">
              <el-icon><Grid /></el-icon>
              {{ row.dataset_name }}
            </span>
          </el-tooltip>
          <span v-else class="ds-id">{{ row.dataset_id ?? '-' }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="num_classes" label="类别数" width="80" align="center">
        <template #default="{ row }">
          <el-tag size="small" effect="plain" type="warning">{{ row.num_classes }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" min-width="170" align="center">
        <template #default="{ row }">{{ row.created_at ? new Date(row.created_at).toLocaleString() : '-' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="260" fixed="right" align="center">
        <template #default="{ row }">
          <div class="row-actions">
            <template v-if="!row.is_active">
              <el-tooltip content="激活该模型版本 (允许多激活并存)" placement="top">
                <el-button
                  size="small"
                  type="success"
                  @click="emit('activate', row.id)"
                >激活</el-button>
              </el-tooltip>
            </template>
            <template v-else>
              <el-tooltip content="取消该模型版本的激活状态" placement="top">
                <el-button
                  size="small"
                  type="info"
                  plain
                  @click="emit('deactivate', row.id)"
                >取消激活</el-button>
              </el-tooltip>
            </template>
            <el-button size="small" @click="emit('detail', row.id)">详情</el-button>
            <el-tooltip content="删除此模型版本 (激活态会同步取消激活)" placement="top">
              <el-button
                size="small"
                type="danger"
                @click="emit('delete', row)"
              >删除</el-button>
            </el-tooltip>
          </div>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
/* 表格 (v2.5.28 行高稳定性改造 + v2.5.29 二级兜底) */
.table-wrapper {
  flex: 1 1 0;
  min-height: 420px;
  overflow: auto;
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  background: #fff;
}
.table-wrapper .data-table {
  height: 100% !important;
  min-height: 420px;
  width: 100% !important;
  font-size: 13px;
}
.data-table :deep(.el-table .el-table__cell) {
  padding: 8px 0 !important;
}
.data-table :deep(.el-table th.el-table__cell) {
  font-size: 13px !important;
  font-weight: 600;
  background: var(--bg-soft) !important;
}

/* v3.0.0 行高闪动第三轮修复 (基于 class 列表截图反推根因):
   根因: Element Plus 检测到 row class 变化后给 el-table 加
         .el-table--enable-row-transition class, 让 <tr> 应用
         transition: all 0.3s. 行高/列宽/padding 任何变化都有
         0.3s 过渡动画 → 视觉上"行高闪动"
   触发场景: row.is_active 切换 + is-scrolling-left 切换 +
            fixed="right" 列对齐重算
   修复: 覆盖 <tr> 的 transition, 禁用几何属性过渡,
        只保留 hover 颜色过渡 (background-color/color/box-shadow) */
.data-table :deep(.el-table--enable-row-transition .el-table__row) {
  transition: background-color 0.2s ease, color 0.2s ease, box-shadow 0.2s ease !important;
}

.model-name-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.model-icon {
  width: 26px;
  height: 26px;
  border-radius: 6px;
  background: var(--gradient-brand);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.model-icon :deep(.el-icon) { font-size: 14px; }
.is-active-name { font-weight: 600; color: var(--text-primary); }

.metric { font-variant-numeric: tabular-nums; font-weight: 500; }
.metric--acc.is-strong { color: #00c48c; font-weight: 600; }

.ds-name {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-primary);
}
.ds-name :deep(.el-icon) { color: #4f7cff; font-size: 13px; }
.ds-id { color: var(--text-placeholder); font-family: var(--font-mono); font-size: 12px; }

.row-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  white-space: nowrap;
  justify-content: center;
}
.row-actions .el-button {
  margin: 4;
  padding: 10px;
  min-height: auto;
  size: large;
}

/* 空状态 */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 40px 0;
  color: var(--text-secondary);
}
.empty-state__icon {
  width: 56px;
  height: 56px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 12px;
}
.empty-state__icon--brand { background: rgba(79, 124, 255, 0.1); color: #4f7cff; }
.empty-state__title { font-size: 14px; font-weight: 500; color: var(--text-primary); margin-bottom: 4px; }
.empty-state__desc { font-size: 12px; color: var(--text-placeholder); }
</style>
