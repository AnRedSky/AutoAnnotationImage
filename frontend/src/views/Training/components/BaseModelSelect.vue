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
          <span class="base-model-option__name">{{ m.name }}</span>
          <span class="base-model-option__meta">
            <!-- 任务类型标签: 复用 taskType.ts 的元数据, 保证与 Datasets 页面视觉一致 -->
            <el-tag
              v-for="t in (m.taskTypes || [])"
              :key="t"
              size="small"
              :type="getTaskTypeMeta(t).type"
              effect="plain"
              style="margin-left: 4px;"
            >
              <el-icon style="margin-right: 2px; vertical-align: -2px;">
                <component :is="getTaskTypeMeta(t).icon" />
              </el-icon>
              {{ getTaskTypeMeta(t).label }}
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
 * 单个基础模型项的元数据
 * - name:        timm 模型名, 作为选中值
 * - framework:   来源框架 (timm / onnx / 用户自定义 等), 浅蓝标签显示
 * - params:      参数量字符串 (如 "25.6M"), 浅黄标签显示
 * - taskTypes:   适用任务类型, 取自数据集 taskType 枚举 (classification / detection / segmentation),
 *                复用 utils/taskType 的元数据渲染彩色 chip, 与 Datasets 页面视觉一致.
 *                当前 BASE_MODELS 全是 timm 分类 backbone, 所以全为 ['classification'],
 *                但保留数组结构以便未来扩展目标检测/分割模型.
 * - description: 适用场景描述, 显示在 el-option 底部, 选中后 el-select 顶部 tooltip 悬停可见
 */
export interface BaseModelOption {
  name: string
  framework?: string
  params?: string
  taskTypes?: string[]
  description?: string
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
 * - 任务类型用 utils/taskType 的中文 label, 多个用「/」分隔
 */
const selectedTooltipText = computed<string>(() => {
  const m = selectedModel.value
  if (!m) return '基础模型, 输出会被归一为「未知」, 谨慎使用'
  const tasks = (m.taskTypes || []).map((t) => getTaskTypeMeta(t).label).join(' / ')
  const taskLine = tasks ? `任务类型: ${tasks}` : ''
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
