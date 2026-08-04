<!--
  AnnotationToolbar.vue (v2.5.7 拆分自 Annotate.vue, v3.5.0 拆分状态卡组)
  ===================================================
  标注工作台顶部工具栏 (页面私有子组件)

  v3.5.0 拆分:
  - 顶部 5 张统计卡 → AnnotationStatusCards.vue
    · 3 张 (待标注/AI 已标/已人工标注) 同时作为「状态筛选」入口
    · 父组件传入 activeStatusFilter, 卡片展示高亮 + emit status-filter-change
  - 当前文件只保留: 控制条 (任务类型/数据集/模型/阈值/启动 AI 预标注)

  Props (页面私有, 接收父组件状态):
    datasets, datasetId, currentTaskTypeRaw
    modelName, threshold, iouThreshold, detectionModelName
    models, finetuneModels, selectedModelId, activeModel, useFinetune
    stats, pendingCount, aiLabeledCount,
    humanConfirmedCount, humanCorrectedCount,
    categories                       // 供状态卡组的「类别」卡 + hover 详情
    autoLabeling, autoLabelProgress, autoLabelProgressMessage
    activeStatusFilter                // v3.5.0: 当前激活的筛选状态 (高亮状态卡)

  Emits:
    dataset-change, task-type-filter-change
    threshold-change, iou-threshold-change
    detection-model-change, selected-model-change
    model-name-change, use-finetune-change
    ai-start
    status-filter-change             // v3.5.0 新增
-->
<template>
  <div>
    <!-- 顶部统计卡组 (v3.5.0: 抽离到 AnnotationStatusCards, 这里仅引用) -->
    <AnnotationStatusCards
      :stats="stats"
      :pending-count="pendingCount"
      :ai-labeled-count="aiLabeledCount"
      :human-confirmed-count="humanConfirmedCount"
      :human-corrected-count="humanCorrectedCount"
      :categories="categories"
      :datasets="datasets"
      :dataset-id="datasetId"
      :active-filter="activeStatusFilter"
      @status-filter-change="(v: any) => emit('status-filter-change', v)"
    />

    <!-- 顶部控制条 -->
    <el-card style="margin-bottom: 16px;">
      <el-form inline>
        <!-- 任务类型筛选 (v2.5.20: 移除"全部任务类型"选项) -->
        <el-form-item label="任务类型">
          <el-select
            :model-value="taskTypeFilter"
            @update:model-value="(v: string) => emit('task-type-filter-change', v)"
            class="app-select"
            style="width: 160px;"
          >
            <el-option
              v-for="opt in TASK_TYPE_FILTER_OPTIONS"
              :key="opt.value"
              :value="opt.value"
              :label="opt.label"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="数据集">
          <el-select
            :model-value="datasetId"
            @update:model-value="(v: number) => emit('dataset-change', v)"
            placeholder="请选择" class="app-select" filterable
            :disabled="filteredDatasets.length === 0"
            style="min-width: 220px;"
          >
            <el-option v-for="d in filteredDatasets" :key="d.id" :label="d.name" :value="d.id" />
            <template #empty>
              <div style="padding: 8px 12px; color: #909399; font-size: 12px;">
                当前任务类型下没有数据集, 请切换任务类型或新建数据集
              </div>
            </template>
          </el-select>
        </el-form-item>
        <el-form-item label="置信度阈值">
          <el-slider
            :model-value="threshold"
            @update:model-value="(v: number) => emit('threshold-change', v)"
            :min="0.1" :max="1.0" :step="0.05" style="width: 160px;"
            :format-tooltip="(v: number) => `${(v * 100).toFixed(0)}%`"
          />
        </el-form-item>
        <!-- 「模型」form-item: 三任务统一交互 -->
        <el-form-item label="模型">
          <el-switch
            :model-value="useFinetune"
            @update:model-value="(v: boolean) => emit('use-finetune-change', v)"
            active-text="项目模型" inactive-text="基础模型"
            inline-prompt style="--el-switch-on-color: #67c23a; margin-right: 8px;"
          />
          <el-tooltip
            v-if="useFinetune"
            :content="activeModel ? '当前激活: ' + activeModel.name : '当前没有激活的模型'"
            placement="top">
            <el-select
              :model-value="selectedModelId"
              @update:model-value="(v: number | null) => emit('selected-model-change', v)"
              class="app-select" style="width: 260px;"
              :fit-input-width="false" popper-class="app-select-dropdown"
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
          <el-tooltip
            v-else-if="currentTaskTypeRaw === 'classification'"
            content="基础模型输出会被归一为「未知」, 请谨慎使用" placement="top"
          >
            <el-select
              :model-value="modelName"
              @update:model-value="(v: string) => emit('model-name-change', v)"
              class="app-select" style="width: 260px;"
              :fit-input-width="false" popper-class="app-select-dropdown"
            >
              <el-option
                v-for="m in baseModelsByTask.classification" :key="m.name"
                :value="m.name" :label="`${m.name} (${m.params || ''})`"
              >
                <div class="annotate-base-option">
                  <el-tag v-if="m.recommended" size="small" type="success" effect="dark"
                    style="font-size: 10px; line-height: 16px; height: 16px; padding: 0 4px;"
                  >推荐</el-tag>
                  <el-tag v-if="m.framework" size="small" type="info" effect="plain">{{ m.framework }}</el-tag>
                  <span class="annotate-base-option__name">{{ m.name }}</span>
                  <span class="annotate-base-option__params">({{ m.params }})</span>
                </div>
                <div v-if="m.description" class="annotate-base-option__desc">适用: {{ m.description }}</div>
              </el-option>
            </el-select>
          </el-tooltip>
          <el-tooltip
            v-else-if="currentTaskTypeRaw === 'detection'"
            content="预训练 YOLO 仅识别 COCO 80 类, 仅与项目类目重合的部分会写入 BBox" placement="top"
          >
            <el-select
              :model-value="detectionModelName"
              @update:model-value="(v: string) => emit('detection-model-change', v)"
              placeholder="选择 YOLO 模型" class="app-select" style="width: 260px;"
              :fit-input-width="false" popper-class="app-select-dropdown"
            >
              <el-option
                v-for="m in baseModelsByTask.detection" :key="m.name"
                :value="m.name" :label="`${m.name} (${m.params || ''})`"
              >
                <div class="annotate-base-option">
                  <el-tag v-if="m.recommended" size="small" type="success" effect="dark"
                    style="font-size: 10px; line-height: 16px; height: 16px; padding: 0 4px;"
                  >推荐</el-tag>
                  <el-tag v-if="m.framework" size="small" type="info" effect="plain">{{ m.framework }}</el-tag>
                  <span class="annotate-base-option__name">{{ m.name }}</span>
                  <span class="annotate-base-option__params">({{ m.params }})</span>
                </div>
                <div v-if="m.description" class="annotate-base-option__desc">适用: {{ m.description }}</div>
              </el-option>
            </el-select>
          </el-tooltip>
          <el-tooltip
            v-else-if="currentTaskTypeRaw === 'segmentation'"
            content="torchvision 预训练 (COCO 21 类), 输出仅与项目类目重合时落标" placement="top"
          >
            <el-select
              :model-value="segmentationModelName"
              @update:model-value="(v: string) => emit('segmentation-model-change', v)"
              placeholder="选择 torchvision 分割模型" class="app-select" style="width: 260px;"
              :fit-input-width="false" popper-class="app-select-dropdown"
            >
              <el-option
                v-for="m in baseModelsByTask.segmentation" :key="m.name"
                :value="m.name" :label="`${m.name} (${m.params || ''})`"
              >
                <div class="annotate-base-option">
                  <el-tag v-if="m.recommended" size="small" type="success" effect="dark"
                    style="font-size: 10px; line-height: 16px; height: 16px; padding: 0 4px;"
                  >推荐</el-tag>
                  <el-tag v-if="m.framework" size="small" type="info" effect="plain">{{ m.framework }}</el-tag>
                  <span class="annotate-base-option__name">{{ m.name }}</span>
                  <span class="annotate-base-option__params">({{ m.params }})</span>
                </div>
                <div v-if="m.description" class="annotate-base-option__desc">适用: {{ m.description }}</div>
              </el-option>
            </el-select>
          </el-tooltip>
          <el-tooltip v-else content="未知任务类型, 请刷新页面" placement="top">
            <el-select :model-value="null" disabled placeholder="未知任务"
              class="app-select" style="width: 260px;" />
          </el-tooltip>
        </el-form-item>
        <el-form-item v-if="currentTaskTypeRaw === 'detection'" label="IoU 阈值 (NMS)">
          <el-slider
            :model-value="iouThreshold"
            @update:model-value="(v: number) => emit('iou-threshold-change', v)"
            :min="0.1" :max="0.95" :step="0.05" style="width: 160px;"
            :format-tooltip="(v: number) => v.toFixed(2)"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary" :icon="MagicStick"
            :loading="autoLabeling"
            @click="emit('ai-start')"
          >启动 AI 预标注</el-button>
        </el-form-item>
      </el-form>
      <!-- v2.5.36: AI 预标注实时进度条 -->
      <el-progress
        v-if="autoLabeling || (autoLabelProgress > 0 && autoLabelProgress < 100)"
        :percentage="autoLabelProgress"
        :status="autoLabelProgress >= 100 ? 'success' : ''"
        :stroke-width="14"
        style="margin-top: 8px;"
      />
      <div v-if="autoLabeling && autoLabelProgressMessage"
        style="margin-top: 4px; font-size: 12px; color: #606266;">
        {{ autoLabelProgressMessage }}
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PropType } from 'vue'
import { MagicStick } from '@element-plus/icons-vue'
// v3.5.0: 状态卡组独立成子组件
import AnnotationStatusCards from './AnnotationStatusCards.vue'
import type { StatusFilterValue } from '@/composables/useAnnotationStatusFilter'

const baseModelsByTask = computed<Record<string, any[]>>(() => {
  const grouped: Record<string, any[]> = {
    classification: [],
    detection: [],
    segmentation: [],
  }
  for (const m of (props.models as any[]) || []) {
    if (m?.task_type && grouped[m.task_type]) grouped[m.task_type].push(m)
  }
  return grouped
})

const TASK_TYPE_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'classification', label: '图片分类' },
  { value: 'detection',      label: '目标检测' },
  { value: 'segmentation',   label: '图片分割' },
]

const props = defineProps({
  datasets: { type: Array as PropType<{ id: number; name: string; task_type?: string; image_count?: number }[]>, required: true },
  datasetId: { type: Number as PropType<number | null>, default: null },
  taskTypeFilter: { type: String, required: true },
  currentTaskTypeRaw: { type: String, required: true },
  modelName: { type: String, required: true },
  threshold: { type: Number, required: true },
  iouThreshold: { type: Number, required: true },
  detectionModelName: { type: String, required: true },
  segmentationModelName: { type: String, required: true },
  models: { type: Array as PropType<{ id?: number; name: string; base_model?: string; accuracy?: number; framework?: string; params?: string }[]>, required: true },
  finetuneModels: { type: Array as PropType<{ id?: number; name: string; base_model?: string; accuracy?: number; framework?: string; params?: string }[]>, required: true },
  selectedModelId: { type: Number as PropType<number | null>, default: null },
  activeModel: { type: Object as PropType<{ id?: number; name: string; base_model?: string; accuracy?: number; params?: string } | null>, default: null },
  useFinetune: { type: Boolean, required: true },
  stats: { type: Object as PropType<any>, default: null },
  pendingCount: { type: Number, required: true },
  aiLabeledCount: { type: Number, required: true },
  humanConfirmedCount: { type: Number, required: true },
  humanCorrectedCount: { type: Number, required: true },
  categories: {
    type: Array as PropType<Array<{
      id: number
      name: string
      color?: string
      sample_count?: number
      human_labeled_count?: number
      ai_labeled_count?: number
    }>>,
    default: () => [],
  },
  autoLabeling: { type: Boolean, required: true },
  autoLabelProgress: { type: Number, default: 0 },
  autoLabelProgressMessage: { type: String, default: '' },
  /** v3.5.0: 当前激活的筛选状态, 透传给 AnnotationStatusCards 用于高亮 */
  activeStatusFilter: { type: String as PropType<StatusFilterValue>, required: true },
})

const emit = defineEmits<{
  (e: 'dataset-change', v: number): void
  (e: 'task-type-filter-change', v: string): void
  (e: 'threshold-change', v: number): void
  (e: 'iou-threshold-change', v: number): void
  (e: 'detection-model-change', v: string): void
  (e: 'segmentation-model-change', v: string): void
  (e: 'selected-model-change', v: number | null): void
  (e: 'model-name-change', v: string): void
  (e: 'use-finetune-change', v: boolean): void
  (e: 'ai-start'): void
  /** v3.5.0: 状态筛选变化 (从 AnnotationStatusCards 卡片点击) */
  (e: 'status-filter-change', v: StatusFilterValue): void
}>()

const filteredDatasets = computed(() => {
  const f = props.taskTypeFilter
  return props.datasets.filter((d) => (d.task_type || 'classification') === f)
})
</script>

<style scoped>
.annotate-base-option {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
}
.annotate-base-option__name {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.annotate-base-option__params {
  color: #909399;
  font-size: 12px;
  flex: 0 0 auto;
}
.annotate-base-option__desc {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.4;
  color: #94a3b8;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
