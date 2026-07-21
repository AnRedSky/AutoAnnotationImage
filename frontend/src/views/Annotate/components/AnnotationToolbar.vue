<!--
  AnnotationToolbar.vue (v2.5.7 拆分自 Annotate.vue)
  ===================================================
  标注工作台顶部工具栏:
  - 5 个统计卡片 (待标注 / AI 已标 / 本轮已标 / 本轮总耗时 / 总累计耗时)
  - 数据集选择 + 任务类型徽章 (popover 详解)
  - 模型选择 (fine-tune / 基础模型)
  - 置信度阈值 + IoU 阈值 + AI 模型
  - 启动 AI 预标注 / 自动 AI 预标注 按钮

  Props (页面私有子组件, 接收父组件状态):
    datasets, datasetId, currentTaskTypeRaw
    modelName, threshold, iouThreshold, detectionModelName
    models, finetuneModels, selectedModelId, activeModel, useFinetune
    stats, pendingCount, aiLabeledCount, sessionStats
    autoLabeling, currentTaskMeta

  Emits:
    dataset-change, threshold-change, iou-threshold-change
    detection-model-change, selected-model-change
    model-name-change, use-finetune-change
    auto-ai-start, ai-start
-->
<template>
  <div>
    <!-- 顶部统计 (5 卡片, 与原结构一致) -->
    <el-row v-if="stats" :gutter="12" style="margin-bottom: 16px;">
      <el-col :span="5">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="待标注" :value="pendingCount" suffix="张"
            :value-style="{ color: '#409eff' }" />
        </el-card>
      </el-col>
      <el-col :span="5">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="AI 已标" :value="aiLabeledCount" suffix="张"
            :value-style="{ color: '#67c23a' }" />
        </el-card>
      </el-col>
      <el-col :span="5">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="本会话已标" :value="sessionStats.confirmed + sessionStats.corrected" suffix="张" />
        </el-card>
      </el-col>
      <el-col :span="5">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="本会话耗时"
            :value="Number((sessionStats.total_time_ms / 1000).toFixed(1))" :precision="1" suffix="秒" />
        </el-card>
      </el-col>
      <el-col :span="4">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="估算 AI 节省" :value="stats.estimated_saved_seconds || 0"
            suffix="秒" :value-style="{ color: '#e6a23c' }" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 顶部控制条 -->
    <el-card style="margin-bottom: 16px;">
      <el-form inline>
        <el-form-item label="数据集">
          <el-select
            :model-value="datasetId"
            @update:model-value="(v: number) => emit('dataset-change', v)"
            placeholder="请选择" class="app-select" filterable
          >
            <el-option v-for="d in datasets" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <!-- 任务类型徽章 (点击看完整定义) -->
        <el-form-item v-if="datasetId" label="任务类型">
          <el-popover
            placement="bottom-start" :width="380" trigger="click" :show-after="0"
          >
            <template #reference>
              <el-tag :type="currentTaskMeta.type" effect="plain" size="small" style="cursor: pointer;">
                <el-icon style="vertical-align: -2px; margin-right: 2px;">
                  <component :is="currentTaskMeta.icon" />
                </el-icon>
                {{ currentTaskMeta.label }}
                <el-icon style="vertical-align: -2px; margin-left: 2px;"><InfoFilled /></el-icon>
              </el-tag>
            </template>
            <div class="task-type-popover">
              <div class="ttp-title">
                <el-icon style="vertical-align: -2px; margin-right: 4px;">
                  <component :is="currentTaskMeta.icon" />
                </el-icon>
                {{ currentTaskMeta.label }}
              </div>
              <div class="ttp-row">
                <span class="ttp-label">任务定义</span>
                <span class="ttp-value">{{ currentTaskMeta.definition }}</span>
              </div>
              <div class="ttp-row">
                <span class="ttp-label">输出粒度</span>
                <span class="ttp-value">{{ currentTaskMeta.output }}</span>
              </div>
              <div class="ttp-row">
                <span class="ttp-label">典型场景</span>
                <span class="ttp-value">{{ currentTaskMeta.scenario }}</span>
              </div>
            </div>
          </el-popover>
        </el-form-item>
        <!-- 分类任务: fine-tune 切换 + 模型选择 -->
        <el-form-item v-if="currentTaskTypeRaw === 'classification'">
          <div class="model-select-slot">
            <el-tooltip
              v-if="useFinetune"
              :content="activeModel ? '当前激活: ' + activeModel.name : '当前没有激活的模型'"
              placement="top">
              <el-select
                :model-value="selectedModelId"
                @update:model-value="(v: number | null) => emit('selected-model-change', v)"
                class="app-select"
                :fit-input-width="false"
                popper-class="app-select-dropdown"
                :disabled="finetuneModels.length === 0"
                :placeholder="finetuneModels.length === 0 ? '选择 fine-tune 模型 (仅本数据集已激活)' : '选择 fine-tune 模型'"
              >
                <el-option
                  v-for="m in finetuneModels" :key="m.id"
                  :value="m.id"
                  :label="`${m.name} · ${m.base_model}`"
                >
                  <div style="display: flex; align-items: center; gap: 6px;">
                    <span>{{ m.name }}</span>
                    <span style="color: #909399; font-size: 12px;">· {{ m.base_model }}</span>
                    <span style="margin-left: auto; color: #67c23a; font-size: 12px;">{{ ((m.accuracy ?? 0) * 100).toFixed(1) }}%</span>
                  </div>
                </el-option>
              </el-select>
            </el-tooltip>
            <el-tooltip v-else content="基础模型输出会被归一为「未知」, 请谨慎使用" placement="top">
              <el-select
                :model-value="modelName"
                @update:model-value="(v: string) => emit('model-name-change', v)"
                class="app-select" :fit-input-width="false" popper-class="app-select-dropdown"
              >
                <el-option v-for="m in models" :key="m.name" :label="`${m.name} (${m.params})`" :value="m.name">
                  <div style="display: flex; align-items: center; gap: 6px;">
                    <el-tag v-if="m.framework" size="small" type="info" effect="plain">{{ m.framework }}</el-tag>
                    <span>{{ m.name }}</span>
                    <span style="color: #909399; font-size: 12px;">({{ m.params }})</span>
                  </div>
                </el-option>
              </el-select>
            </el-tooltip>
          </div>
        </el-form-item>
        <el-form-item label="置信度阈值">
          <el-slider
            :model-value="threshold"
            @update:model-value="(v: number) => emit('threshold-change', v)"
            :min="0.1" :max="1.0" :step="0.05" style="width: 160px;"
            :format-tooltip="(v: number) => `${(v * 100).toFixed(0)}%`"
          />
        </el-form-item>
        <el-form-item v-if="currentTaskTypeRaw === 'classification'" label="是否使用项目训练模型">
          <el-switch
            :model-value="useFinetune"
            @update:model-value="(v: boolean) => emit('use-finetune-change', v)"
            active-text="是" inactive-text="否"
            inline-prompt style="--el-switch-on-color: #67c23a;"
          />
        </el-form-item>
        <el-form-item v-if="currentTaskTypeRaw === 'detection'" label="IoU 阈值 (NMS)">
          <el-slider
            :model-value="iouThreshold"
            @update:model-value="(v: number) => emit('iou-threshold-change', v)"
            :min="0.1" :max="0.95" :step="0.05" style="width: 160px;"
            :format-tooltip="(v: number) => v.toFixed(2)"
          />
        </el-form-item>
        <el-form-item v-if="currentTaskTypeRaw === 'detection'" label="AI 模型">
          <el-select
            :model-value="detectionModelName"
            @update:model-value="(v: string) => emit('detection-model-change', v)"
            placeholder="选择 YOLO 模型" size="small" style="width: 160px;"
          >
            <el-option v-for="m in DETECTION_MODELS" :key="m" :value="m" :label="m" />
          </el-select>
        </el-form-item>
      </el-form>
      <div style="margin-top: 8px; display: flex; gap: 8px; align-items: center;">
        <el-button
          type="primary" :icon="MagicStick"
          :loading="autoLabeling"
          @click="emit('ai-start')"
        >启动 AI 预标注</el-button>
        <el-button
          :icon="Lightning"
          @click="emit('auto-ai-start')"
        >自动 AI 预标注</el-button>
        <el-tag v-if="activeModel" type="success" effect="plain" size="small">
          当前激活: {{ activeModel.name }}
        </el-tag>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { InfoFilled, MagicStick, Lightning } from '@element-plus/icons-vue'

const DETECTION_MODELS = ['yolov8n', 'yolov8s', 'yolov8m', 'yolov8l', 'yolov8x']

interface Dataset { id: number; name: string }
interface Model { id?: number; name: string; base_model?: string; accuracy?: number; framework?: string; params?: string }
interface TaskTypeMeta {
  type: string
  label: string
  icon: any
  definition: string
  output: string
  scenario: string
}

defineProps<{
  datasets: Dataset[]
  datasetId: number | null
  currentTaskTypeRaw: string
  currentTaskMeta: TaskTypeMeta
  modelName: string
  threshold: number
  iouThreshold: number
  detectionModelName: string
  models: Model[]
  finetuneModels: Model[]
  selectedModelId: number | null
  activeModel: Model | null
  useFinetune: boolean
  stats: any
  pendingCount: number
  aiLabeledCount: number
  sessionStats: { confirmed: number; corrected: number; total_time_ms: number }
  autoLabeling: boolean
}>()

const emit = defineEmits<{
  (e: 'dataset-change', v: number): void
  (e: 'threshold-change', v: number): void
  (e: 'iou-threshold-change', v: number): void
  (e: 'detection-model-change', v: string): void
  (e: 'selected-model-change', v: number | null): void
  (e: 'model-name-change', v: string): void
  (e: 'use-finetune-change', v: boolean): void
  (e: 'ai-start'): void
  (e: 'auto-ai-start'): void
}>()
</script>

<style scoped>
.stat-card { text-align: center; }
.task-type-popover { padding: 4px 8px; }
.ttp-title { font-size: 14px; font-weight: 600; color: #303133; margin-bottom: 8px; }
.ttp-row { display: flex; gap: 8px; margin-bottom: 6px; font-size: 12px; line-height: 1.6; }
.ttp-label { color: #909399; min-width: 64px; flex-shrink: 0; }
.ttp-value { color: #303133; }
.model-select-slot { display: flex; align-items: center; gap: 8px; }
</style>
