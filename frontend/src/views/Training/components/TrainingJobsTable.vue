<script setup lang="ts">
/**
 * TrainingJobsTable - 训练任务列表表格
 *
 * v3.0.0 Phase J 拆分: 从 Training/index.vue 抽离
 * - 表格 (多选 + 任务类型/状态/设备/进度/时间列 + 行操作)
 * - 行内: 启动/暂停/继续/再训练 合并按钮 + 取消 + 详情 + 删除
 * - 占位行 (等 worker 写库)
 *
 * 父组件:
 *   <TrainingJobsTable
 *     :data="jobs" :loading="loading" :action-pending="actionPending"
 *     :state-label="..." :index-method="..." :dataset-name-of="..."
 *     :device-tag-type="..." :device-short-label="..." :device-tooltip="..."
 *     :format-time="..." :progress-status="..."
 *     :run-btn-label="..." :run-btn-type="..." :run-btn-action="..." :run-btn-disabled="..."
 *     :can-cancel="..." :can-delete="..." :can-start="..."
 *     @selection-change="..." @row-run="..." @row-cancel="..." @row-detail="..." @row-delete="..."
 *   />
 */
import { Promotion, VideoPlay, VideoPause, CircleClose, View, Delete } from '@element-plus/icons-vue'
import { getTaskTypeMeta } from '@/utils/taskType'
import { getPretrainModeMeta } from '@/utils/pretrainMode'
import StateBadge from './StateBadge.vue'

defineProps<{
  data: any[]
  loading: boolean
  actionPending: Record<number, string>
  indexMethod: (idx: number) => number
  datasetNameOf: (id: number) => string
  deviceTagType: (t: any) => string
  deviceShortLabel: (t: any, n: any) => string
  deviceTooltip: (info: any) => string
  formatTime: (iso: any) => string
  progressStatus: (s: string) => any
  runBtnLabel: (s: string) => string
  runBtnType: (s: string) => any
  runBtnAction: (s: string) => string
  runBtnDisabled: (s: string) => boolean
  canCancel: (s: string) => boolean
  canDelete: (s: string) => boolean
  canStart: (s: string) => boolean
}>()

const emit = defineEmits<{
  (e: 'selection-change', rows: any[]): void
  (e: 'row-run', row: any): void
  (e: 'row-cancel', row: any): void
  (e: 'row-detail', row: any): void
  (e: 'row-delete', row: any): void
}>()

const rowClassName = ({ row }: { row: any }) => {
  return row?.is_placeholder ? 'row--placeholder' : ''
}
</script>

<template>
  <div class="table-wrapper">
    <el-table
      :data="data"
      v-loading="loading"
      border stripe
      class="jobs-table"
      height="100%"
      style="width: 100%;"
      :row-class-name="rowClassName"
      @selection-change="(rows: any[]) => emit('selection-change', rows)"
    >
      <template #empty>
        <div class="empty-state">
          <div class="empty-state__icon empty-state__icon--brand">
            <el-icon><Promotion /></el-icon>
          </div>
          <div class="empty-state__title">还没有训练任务</div>
          <div class="empty-state__desc">
            点击右上角「新建训练任务」, 选定数据集后即可启动微调
          </div>
        </div>
      </template>
      <el-table-column type="index" :index="indexMethod" label="#" width="42" align="center" />
      <el-table-column type="selection" width="40" :reserve-selection="false" />
      <el-table-column label="数据集" min-width="92" show-overflow-tooltip>
        <template #default="{ row }">{{ datasetNameOf(row.dataset_id) }}</template>
      </el-table-column>
      <el-table-column prop="base_model" label="基础模型" min-width="92" show-overflow-tooltip />
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
      <!-- v3.0.0: 训练模式 chip 列 (追溯: 基础 / 增量) -->
      <el-table-column label="训练模式" width="100" align="center">
        <template #default="{ row }">
          <el-tag
              :type="getPretrainModeMeta(row.pretrain_mode).type"
              effect="plain"
              size="small"
            >
              {{ getPretrainModeMeta(row.pretrain_mode).label }}
            </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="model_name" label="模型版本" min-width="118" show-overflow-tooltip />
      <el-table-column label="设备" min-width="118" show-overflow-tooltip>
        <template #default="{ row }">
          <el-tooltip
            v-if="row.device_info"
            placement="top"
            :content="deviceTooltip(row.device_info) +
              (row.gpu_peak_memory_mb
                ? `\n\nGPU 峰值显存: ${row.gpu_peak_memory_mb} MB`
                : '')">
            <el-tag :type="deviceTagType(row.device_type)" size="small">
              {{ deviceShortLabel(row.device_type, row.device_name) }}
            </el-tag>
          </el-tooltip>
          <span v-else style="color: #c0c4cc;">-</span>
          <el-tag
            v-if="row.gpu_peak_memory_mb"
            size="small" type="warning" effect="plain"
            style="margin-left: 4px;"
          >{{ row.gpu_peak_memory_mb }}MB</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="epochs" label="轮次" width="80" align="center" />
      <el-table-column label="状态" width="86" align="center">
        <template #default="{ row }">
          <StateBadge :state="row.state" />
        </template>
      </el-table-column>
      <el-table-column label="进度" min-width="100">
        <template #default="{ row }">
          <el-progress
            :percentage="Math.round(row.progress || 0)"
            :status="progressStatus(row.state)"
            :stroke-width="6"
          />
        </template>
      </el-table-column>
      <el-table-column label="创建日期" min-width="92" show-overflow-tooltip>
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="开始日期" min-width="92" show-overflow-tooltip>
        <template #default="{ row }">
          {{ (row.state === 'PENDING' || row.state === 'PAUSED') ? '-' : formatTime(row.started_at) }}
        </template>
      </el-table-column>
      <el-table-column label="结束日期" min-width="92" show-overflow-tooltip>
        <template #default="{ row }">
          {{ ['SUCCESS', 'FAILURE', 'REVOKED'].includes(row.state) ? formatTime(row.finished_at) : '-' }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right" align="center">
        <template #default="{ row }">
          <div class="row-actions">
            <el-tooltip :content="runBtnLabel(row.state)" placement="top" :show-after="200">
              <el-button
                size="small"
                :icon="row.state === 'PROGRESS' ? VideoPause : VideoPlay"
                circle plain
                :type="runBtnType(row.state)"
                :disabled="row.is_placeholder || runBtnDisabled(row.state)"
                :loading="actionPending[row.id] === runBtnAction(row.state)"
                @click="emit('row-run', row)"
              />
            </el-tooltip>
            <el-tooltip content="取消训练" placement="top" :show-after="200">
              <el-button
                size="small"
                :icon="CircleClose"
                circle plain
                type="danger"
                :disabled="row.is_placeholder || !canCancel(row.state)"
                :loading="actionPending[row.id] === 'cancel'"
                @click="emit('row-cancel', row)"
              />
            </el-tooltip>
            <el-tooltip content="查看详情" placement="top" :show-after="200">
              <el-button
                size="small"
                :icon="View"
                circle plain
                type="primary"
                :disabled="row.is_placeholder"
                @click="emit('row-detail', row)"
              />
            </el-tooltip>
            <el-tooltip content="删除任务" placement="top" :show-after="200">
              <el-button
                size="small"
                :icon="Delete"
                circle plain
                type="danger"
                :disabled="row.is_placeholder || !canDelete(row.state)"
                :loading="actionPending[row.id] === 'delete'"
                @click="emit('row-delete', row)"
              />
            </el-tooltip>
          </div>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
/* 表格区: 占据所有剩余高度, 内部滚动, 不挤压分页栏 */
.table-wrapper {
  flex: 1 1 0;
  min-height: 420px;
  overflow: auto;
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  background: #fff;
}
.table-wrapper .el-table {
  height: 100% !important;
  width: 100% !important;
  font-size: 13px;
}

/* 占位行 (乐观插入, 等 worker 写库) */
.jobs-table :deep(.row--placeholder) {
  background: linear-gradient(90deg,
    rgba(79, 124, 255, 0.06) 0%,
    rgba(79, 124, 255, 0.02) 50%,
    rgba(79, 124, 255, 0.06) 100%) !important;
  background-size: 200% 100%;
  font-style: italic;
  color: var(--text-secondary);
  animation: placeholder-shimmer 2s ease-in-out infinite;
}
.jobs-table :deep(.row--placeholder td) {
  position: relative;
}
.jobs-table :deep(.row--placeholder td:first-child::before) {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--gradient-brand);
  border-radius: 0 3px 3px 0;
}
@keyframes placeholder-shimmer {
  0%, 100% { background-position: 0% 50%; }
  50% { background-position: 100% 50%; }
}

/* 单元格内边距: 默认 12px 0 偏大, 压缩到 8px */
.jobs-table :deep(.el-table .el-table__cell) {
  padding: 8px 0 !important;
}
.jobs-table :deep(.el-table th.el-table__cell) {
  font-size: 13px !important;
  font-weight: 600;
  background: var(--bg-soft) !important;
}

/* 行操作按钮组 */
.row-actions {
  display: inline-flex;
  align-items: center;
  gap: 16px;
  white-space: nowrap;
  justify-content: center;
}
.row-actions .el-button {
  margin: 0;
  padding: 4px;
  min-height: auto;
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
