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
          <el-tag
            v-if="m.framework"
            size="small"
            type="info"
            effect="plain"
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
    </el-option>
  </el-select>
</template>

<script setup lang="ts">
/**
 * 单个基础模型项的元数据
 * - name:     timm 模型名, 作为选中值
 * - framework: 来源框架 (timm / onnx / 用户自定义 等), 浅蓝标签显示
 * - params:   参数量字符串 (如 "25.6M"), 浅黄标签显示
 */
export interface BaseModelOption {
  name: string
  framework?: string
  params?: string
}

withDefaults(defineProps<{
  modelValue: string
  models: BaseModelOption[]
  placeholder?: string
}>(), {
  placeholder: '请选择模型'
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
</style>
