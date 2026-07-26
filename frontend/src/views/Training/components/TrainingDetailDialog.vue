<script setup lang="ts">
/**
 * TrainingDetailDialog - 训练任务详情弹窗 (含 SSE 实时进度 + 历史曲线)
 *
 * v3.0.0 Phase J 拆分: 从 Training/index.vue 抽离
 * - 顶部 hero 信息 + 基本信息 (descriptions)
 * - 数据集统计 (SSE 实时 + DB fallback, 4 联指标)
 * - 增量训练状态 (再训练时, 启动那一刻 SSE 推过来)
 * - 实时进度区 (进度条 + 4 联指标 + ECharts 训练曲线 + 日志面板)
 * - 底部操作栏: 启动/暂停/取消/删除 (复用 useTrainingActions 提供的行操作)
 * - 错误信息 (FAILURE 状态) 双源展示 (DB.error + Redis 兜底)
 *
 * 父组件只需:
 *   <TrainingDetailDialog
 *     v-model:visible="detailVisible"
 *     :row="detailRow"
 *     :dataset-name-of="datasetNameOf"
 *     :action-pending="actionPending"
 *     :can-start="canStart" :can-cancel="canCancel" :can-delete="canDelete"
 *     :run-btn-label="..." :run-btn-type="..." :run-btn-action="..."
 *     :format-time="..." :format-duration="..." :device-tag-type="..." :device-short-label="..." :device-tooltip="..."
 *     :progress-status="..." :state-label="..."
 *     @run="onRunBtnClick" @cancel="onRowCancel" @delete="onRowDelete" @reload="loadJobs"
 *   />
 */
import { computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { VideoPlay, InfoFilled } from '@element-plus/icons-vue'
import { getTaskTypeMeta } from '@/utils/taskType'
import StateBadge from './StateBadge.vue'
import { useTrainingDetailStream } from '@/composables/useTrainingDetailStream'

const props = defineProps<{
  visible: boolean
  row: any
  datasetNameOf: (id: number) => string
  actionPending: Record<number, string>
  canStart: (s: string) => boolean
  canCancel: (s: string) => boolean
  canDelete: (s: string) => boolean
  runBtnLabel: (s: string) => string
  runBtnType: (s: string) => any
  runBtnAction: (s: string) => string
  formatTime: (iso: any) => string
  formatDuration: (s: number) => string
  deviceTagType: (t: any) => string
  deviceShortLabel: (t: any, n: any) => string
  deviceTooltip: (info: any) => string
  progressStatus: (s: string) => any
  stateLabel: (s: string) => string
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'run', row: any): void
  (e: 'cancel', row: any): void
  (e: 'delete', row: any): void
  (e: 'reload'): void
}>()

// ============== 详情数据 + SSE 实时流 (委托 useTrainingDetailStream) ==============
const {
  job, error, progress, state, currentEpoch, totalEpochs, message, log, history, chartEl,
  displayDataTotal, displayDataTrain, displayDataVal, displayNumClasses, displayClassNames,
  pretrainedLoaded, pretrainedPath, pretrainedError,
  openDetail, cleanup, onHistoryChanged,
} = useTrainingDetailStream()

// 双向绑定 visible
const innerVisible = computed({
  get: () => props.visible,
  set: (v) => emit('update:visible', v),
})

// 打开时: 同步 row → 拉 DB + SSE
watch(() => props.row, async (r) => {
  if (r && props.visible) {
    await openDetail(r)
    await nextTick()
    onHistoryChanged()
  }
}, { immediate: false })

// visible 从 true → false 时清理资源
watch(() => props.visible, async (v) => {
  if (!v) {
    cleanup()
  }
})

// 监听 history 变化, 同步更新图表
watch(history, () => {
  onHistoryChanged()
}, { deep: true })

onBeforeUnmount(() => {
  cleanup()
})

// 详情弹窗内部的操作按钮回调 (向上抛出事件, 由父组件统一处理状态变更)
const onRunClick = (row: any) => {
  emit('run', row)
  emit('reload')
}
const onCancelClick = (row: any) => {
  emit('cancel', row)
}
const onDeleteClick = (row: any) => {
  emit('delete', row)
  // 删除后, 关闭弹窗 (防止内部 detail 状态与外部 list 状态不一致)
  innerVisible.value = false
}

const onCloseDialog = () => {
  innerVisible.value = false
}
</script>

<template>
  <el-dialog
    :model-value="innerVisible"
    :title="job ? `训练任务详情  #${job.id}  (${stateLabel(job.state)})` : '训练任务详情'"
    width="880px"
    destroy-on-close
    @update:model-value="(v: boolean) => innerVisible = v"
    @close="onCloseDialog"
  >
    <div v-if="job">
      <!-- 顶部信息条 -->
      <div class="detail-hero">
        <div class="hero-left">
          <div class="hero-mark">
            <el-icon><VideoPlay /></el-icon>
          </div>
          <div>
            <div class="hero-name">{{ job.model_name }}</div>
            <div class="hero-base">{{ job.base_model }} · {{ datasetNameOf(job.dataset_id) }}</div>
          </div>
        </div>
        <StateBadge :state="job.state" size="md" />
      </div>

      <!-- 基本信息 -->
      <el-descriptions
        class="detail-descs"
        :column="3"
        border
        size="small"
        label-class-name="detail-desc-label"
        class-name="detail-desc-content"
      >
        <el-descriptions-item label="任务 ID">{{ job.id }}</el-descriptions-item>
        <el-descriptions-item label="数据集">{{ datasetNameOf(job.dataset_id) }}</el-descriptions-item>
        <el-descriptions-item label="任务类型">
          <el-tag
            v-if="job.task_type"
            :type="(getTaskTypeMeta(job.task_type)?.type) || 'info'"
            size="small"
            effect="light"
          >
            {{ getTaskTypeMeta(job.task_type)?.label || job.task_type }}
          </el-tag>
          <span v-else style="color: #c0c4cc;">未记录</span>
        </el-descriptions-item>
        <el-descriptions-item label="基础模型">{{ job.base_model }}</el-descriptions-item>
        <el-descriptions-item label="模型版本">{{ job.model_name }}</el-descriptions-item>
        <el-descriptions-item label="训练设备">
          <div class="device-info-cell">
            <el-tag
              v-if="job.device_type"
              :type="deviceTagType(job.device_type)"
              size="small"
              class="device-tag"
            >
              {{ deviceShortLabel(job.device_type, job.device_name) }}
            </el-tag>
            <span v-else class="device-empty">未记录</span>
            <el-tooltip
              v-if="job.device_info"
              placement="top"
              :content="deviceTooltip(job.device_info) +
                (job.gpu_peak_memory_mb
                  ? `\n\nGPU 峰值显存: ${job.gpu_peak_memory_mb} MB`
                  : '')">
              <el-icon class="device-info-icon"><InfoFilled /></el-icon>
            </el-tooltip>
            <el-tag
              v-if="job.gpu_peak_memory_mb"
              size="small"
              type="warning"
              effect="plain"
              class="device-peak-tag"
            >峰值 {{ job.gpu_peak_memory_mb }} MB</el-tag>
          </div>
        </el-descriptions-item>
        <el-descriptions-item label="轮次">{{ job.epochs }}</el-descriptions-item>
        <el-descriptions-item label="批大小">{{ job.batch_size }}</el-descriptions-item>
        <el-descriptions-item label="学习率">{{ job.learning_rate }}</el-descriptions-item>
        <el-descriptions-item label="耗时(s)">
          <span v-if="job.duration_seconds">{{ job.duration_seconds.toFixed(1) }}</span>
          <span v-else style="color: #c0c4cc;">-</span>
          <span
            v-if="job.duration_seconds >= 60"
            style="color: var(--text-secondary); font-size: 12px; margin-left: 4px;"
          >
            ({{ formatDuration(job.duration_seconds) }})
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="开始时间">{{ formatTime(job.started_at) }}</el-descriptions-item>
        <el-descriptions-item label="结束时间">{{ formatTime(job.finished_at) }}</el-descriptions-item>
        <el-descriptions-item label="Celery task_id" :span="3">
          <code style="word-break: break-all; font-family: var(--font-mono); font-size: 12px;">
            {{ job.celery_task_id || '-' }}
          </code>
        </el-descriptions-item>
      </el-descriptions>

      <!-- 错误信息 (FAILURE 状态) -->
      <div v-if="error" style="margin-top: 16px;">
        <el-divider content-position="left">错误详情</el-divider>
        <el-alert
          :title="`DB.error: ${error.db_error || '(无)'}`"
          type="error" :closable="false" show-icon
          style="margin-bottom: 8px;"
        />
        <el-alert
          v-if="error.redis_error"
          :title="`Redis 兜底: ${JSON.stringify(error.redis_error)}`"
          type="warning" :closable="false" show-icon
        />
      </div>

      <!-- 数据集统计 (SSE 实时 + DB fallback) -->
      <el-divider content-position="left">数据集统计</el-divider>
      <el-row :gutter="12" style="margin-bottom: 8px;">
        <el-col :span="6">
          <div class="mini-stat mini-stat--blue">
            <div class="mini-label">总样本数</div>
            <div class="mini-value">{{ displayDataTotal ?? 0 }} <span class="mini-unit">张</span></div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="mini-stat mini-stat--green">
            <div class="mini-label">训练集</div>
            <div class="mini-value">{{ displayDataTrain ?? 0 }} <span class="mini-unit">张</span></div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="mini-stat mini-stat--orange">
            <div class="mini-label">验证集</div>
            <div class="mini-value">{{ displayDataVal ?? 0 }} <span class="mini-unit">张</span></div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="mini-stat mini-stat--red">
            <div class="mini-label">类别数</div>
            <div class="mini-value">{{ displayNumClasses ?? 0 }} <span class="mini-unit">类</span></div>
          </div>
        </el-col>
      </el-row>
      <div
        v-if="displayClassNames.length > 0"
        class="class-names-row"
      >
        类别:
        <el-tag
          v-for="cn in displayClassNames" :key="cn"
          size="small" type="info" effect="plain" class="class-name-tag"
        >{{ cn }}</el-tag>
      </div>
      <div
        v-else
        class="class-names-row class-names-row--empty"
      >
        等待 worker 启动并加载数据集后显示...
      </div>

      <!-- 增量训练状态 (再训练时, 启动那一刻 SSE 推过来) -->
      <el-alert
        v-if="pretrainedLoaded === true"
        type="success" :closable="false" show-icon
        :title="`✓ 增量训练: 已加载模型权重 ${pretrainedPath || ''}`"
        style="margin-bottom: 8px;"
      />
      <el-alert
        v-else-if="pretrainedLoaded === false"
        type="warning" :closable="false" show-icon
        :title="`⚠ 增量训练: 未加载历史权重 (${pretrainedError || '原因未知'}), 改为随机初始化 / ImageNet 预训练`"
        style="margin-bottom: 8px;"
      />

      <!-- 实时进度区 -->
      <el-divider content-position="left">训练进度</el-divider>
      <el-row :gutter="12" style="margin-bottom: 8px;">
        <el-col :span="6">
          <div class="mini-stat mini-stat--blue">
            <div class="mini-label">进度</div>
            <div class="mini-value">{{ progress.toFixed(1) }}<span class="mini-unit">%</span></div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="mini-stat mini-stat--green">
            <div class="mini-label">当前 epoch</div>
            <div class="mini-value">{{ currentEpoch || 0 }}</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="mini-stat mini-stat--orange">
            <div class="mini-label">总轮次</div>
            <div class="mini-value">{{ totalEpochs || 0 }}</div>
          </div>
        </el-col>
        <el-col :span="6">
          <div class="mini-stat mini-stat--red">
            <div class="mini-label">已记录 epoch</div>
            <div class="mini-value">{{ history.length }}</div>
          </div>
        </el-col>
      </el-row>
      <el-progress
        :percentage="progress"
        :status="progressStatus(state)"
        :stroke-width="14"
      />
      <div class="detail-msg">
        {{ message || '等待 worker 启动...' }}
      </div>
      <div ref="chartEl" class="training-chart-box"></div>
      <pre class="log-box">{{ log.join('\n') }}</pre>
    </div>

    <template #footer>
      <el-button
        v-if="job && (job.state === 'PROGRESS' || job.state === 'PENDING' || job.state === 'PAUSED' || canStart(job.state))"
        :type="runBtnType(job.state)"
        :loading="actionPending[job.id] === runBtnAction(job.state)"
        @click="onRunClick(job)"
      >{{ runBtnLabel(job.state) }}</el-button>
      <el-button
        v-if="job && canCancel(job.state)"
        type="danger"
        :loading="actionPending[job.id] === 'cancel'"
        @click="onCancelClick(job)"
      >取消</el-button>
      <el-button
        v-if="job && canDelete(job.state)"
        type="danger" link
        :loading="actionPending[job.id] === 'delete'"
        @click="onDeleteClick(job)"
      >删除</el-button>
      <el-button @click="onCloseDialog">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
/* 详情对话框: 顶部信息区与指标块 */
.detail-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-radius: var(--radius-md);
  background: linear-gradient(135deg, rgba(79, 124, 255, 0.06) 0%, rgba(110, 81, 233, 0.06) 100%);
  border: 1px solid rgba(79, 124, 255, 0.12);
  margin-bottom: 12px;
}
.hero-left { display: flex; align-items: center; gap: 12px; }
.hero-mark {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  background: var(--gradient-brand);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  box-shadow: 0 4px 12px rgba(79, 124, 255, 0.3);
}
.hero-name { font-size: 15px; font-weight: 600; color: var(--text-primary); }
.hero-base { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }

/* 描述列表: 固定列宽, 长文本在单元格内换行, 不挤压相邻列 */
.detail-descs :deep(.el-descriptions__table) {
  table-layout: fixed;
  width: 100%;
}
.detail-descs :deep(.detail-desc-label) {
  color: var(--text-secondary) !important;
  font-weight: 500;
  background: var(--bg-soft) !important;
  width: 96px;
  white-space: nowrap;
  vertical-align: top;
}
.detail-descs :deep(.detail-desc-content) {
  word-break: break-word;
  overflow-wrap: anywhere;
  vertical-align: top;
}

/* 训练设备: 长设备名在固定宽度单元格内换行 */
.device-info-cell {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 6px;
  min-width: 0;
  max-width: 100%;
}
.device-info-cell :deep(.device-tag),
.device-info-cell :deep(.device-peak-tag) {
  white-space: normal;
  height: auto;
  line-height: 1.4;
  word-break: break-word;
}
.device-empty {
  color: #c0c4cc;
}
.device-info-icon {
  cursor: help;
  flex-shrink: 0;
}

/* 详情内的小型指标块 (用于 4 联指标和数据集统计) */
.mini-stat {
  background: var(--bg-soft);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  border: 1px solid var(--border-soft);
  text-align: center;
  height: 100%;
}
.mini-stat .mini-label {
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 4px;
}
.mini-stat .mini-value {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}
.mini-stat .mini-unit {
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 400;
}
.mini-stat--blue   .mini-value { color: #4f7cff; }
.mini-stat--green  .mini-value { color: #00c48c; }
.mini-stat--orange .mini-value { color: #ff8a4c; }
.mini-stat--red    .mini-value { color: #ff4d4f; }

.class-names-row {
  margin-bottom: 8px;
  color: #606266;
  font-size: 13px;
}
.class-names-row--empty {
  color: #C0C4CC;
}
.class-name-tag {
  margin-right: 4px;
}

.detail-msg {
  margin-top: 6px;
  color: #909399;
  font-size: 13px;
}

.training-chart-box { width: 100%; height: 300px; margin-top: 12px; }
.log-box {
  background: #0a0a0a;
  color: #0f0;
  padding: 12px;
  max-height: 200px;
  overflow: auto;
  margin-top: 12px;
  font-size: 12px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  white-space: pre-wrap;
  word-break: break-all;
  border: 1px solid #222;
}
</style>
