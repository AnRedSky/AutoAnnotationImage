<!--
  BaseModelSelect.vue
  基础模型下拉选择器 — 供 AI 预标注 / 新建训练任务 共用
  -------------------------------------------------------------------------
  设计要点:
    - 数据双向绑定 (v-model: modelValue) — 组件只是 form 的"视图", 不持有数据
    - 模型列表通过 props 注入 — 组件不耦合 modelApi / autoAnnotateApi
    - 统一的 flex 布局: name + framework 标签 + params 标签
    - 修复: 之前 TrainingParamsForm 的内联版本与 DatasetDetail 的
            <el-option> 写法不一致, 导致下拉项显示为 "resnet18 timm" 或
            JSON 对象. 现在两边都走这个组件, 样式 1:1 对齐.
-->
<template>
  <el-tooltip
    placement="top"
    :show-after="200"
    :content="selectedTooltipText"
  >
    <el-select
      :model-value="modelValue"
      :placeholder="placeholder"
      :style="{ width: '100%' }"
      @update:model-value="(v: string) => emit('update:modelValue', v)"
    >
      <el-option
        v-for="m in models"
        :key="m.name"
        :value="m.name"
        :label="m.name"
      >
        <div class="base-model-option">
          <span class="base-model-option__name">
            {{ m.name }}
            <!-- v2.5.47: 推荐标识 — 后端 recommended=True 时显示浅色高亮, 引导用户选推荐模型 -->
            <el-tag
              v-if="m.recommended"
              size="small" type="success" effect="dark"
              style="margin-left: 6px; font-size: 10px; line-height: 16px; height: 16px; padding: 0 4px;"
            >推荐</el-tag>
          </span>
          <span class="base-model-option__meta">
            <!-- 任务类型 chip: 单一值, 与后端 /models task_type 字段对齐 -->
            <el-tag
              v-if="m.task_type"
              size="small"
              :type="getTaskTypeMeta(m.task_type).type"
              effect="plain"
              style="margin-left: 4px;"
            >
              <el-icon style="margin-right: 2px; vertical-align: -2px;">
                <component :is="getTaskTypeMeta(m.task_type).icon" />
              </el-icon>
              {{ getTaskTypeMeta(m.task_type).label }}
            </el-tag>
            <el-tag
              v-if="m.framework"
              size="small"
              type="info"
              effect="plain"
              style="margin-left: 4px;"
            >{{ m.framework }}</el-tag>
            <el-tag
              v-if="m.params"
              size="small"
              type="warning"
              effect="plain"
              style="margin-left: 4px;"
            >{{ m.params }}</el-tag>
          </span>
        </div>
        <div
          v-if="m.description"
          class="base-model-option__desc"
        >适用: {{ m.description }}</div>
      </el-option>
    </el-select>
  </el-tooltip>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { getTaskTypeMeta } from '@/utils/taskType'

/**
 * 单个基础模型项的元数据 (v2.5.47: 与后端 /api/auto-annotate/models 对齐)
 * - name:        模型名, 作为选中值
 * - framework:   来源框架 (timm / ultralytics / torchvision 等), 浅蓝标签显示
 * - params:      参数量字符串 (如 "25.6M"), 浅黄标签显示
 * - task_type:   适用任务类型 (单一值, 取自后端 /models 字段), 复用 utils/taskType 渲染彩色 chip
 *                后端返回的 task_type 是字符串而非数组, 与此前 hand-crafted 列表的
 *                taskTypes[] 不再兼容, 全部统一为单一字符串
 * - description: 适用场景描述, 显示在 el-option 底部, 选中后 el-select 顶部 tooltip 悬停可见
 * - recommended: 后端 recommended=True 的推荐项, UI 浅绿"推荐"标签提示
 * - imagenet_top1 / coco_mAP50 / coco_mIoU: 预训练指标, 选型参考用, 此处不在 UI 渲染
 */
export interface BaseModelOption {
  name: string
  framework?: string
  params?: string
  task_type?: 'classification' | 'detection' | 'segmentation' | string
  description?: string
  recommended?: boolean
  imagenet_top1?: number
  coco_mAP50?: number
  coco_mIoU?: number
}

const props = withDefaults(defineProps<{
  modelValue: string
  models: BaseModelOption[]
  placeholder?: string
}>(), {
  placeholder: '请选择模型'
})

// 当前选中的模型对象, 用于 el-select 顶部 tooltip
const selectedModel = computed<BaseModelOption | undefined>(() =>
  props.models.find((m) => m.name === props.modelValue)
)

/**
 * el-select 顶部 tooltip 文本拼接
 * 格式: [任务类型] | [适用场景]
 * - 无 description 时降级为「基础模型, 输出会被归一为「未知」, 谨慎使用」(与 Annotate 一致)
 * - 任务类型用 utils/taskType 的中文 label
 */
const selectedTooltipText = computed<string>(() => {
  const m = selectedModel.value
  if (!m) return '基础模型, 输出会被归一为「未知」, 谨慎使用'
  const t = m.task_type ? getTaskTypeMeta(m.task_type).label : ''
  const taskLine = t ? `任务类型: ${t}` : ''
  const descLine = m.description ? `适用: ${m.description}` : ''
  if (taskLine && descLine) return `${taskLine}\n${descLine}`
  return taskLine || descLine || '基础模型, 输出会被归一为「未知」, 谨慎使用'
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()
</script>

<style scoped>
/*
 * 与 TrainingParamsForm 保持完全一致的样式
 * 强制 flex, 避免 el-tag 在 popper 容器里浮动失效的问题
 */
.base-model-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 8px;
}
.base-model-option__name {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.base-model-option__meta {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
}
.base-model-option__desc {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.4;
  color: #94a3b8;
  /* 长文本省略: 下拉行高度可控, 避免长 description 撑爆 popper */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
