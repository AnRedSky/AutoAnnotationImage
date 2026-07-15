<!--
  TrainingParamsForm.vue
  训练参数表单 — 供"新建任务" / "修改参数并启动"两个弹窗复用
  -------------------------------------------------------------------------
  抽离原因:
    1. 两个弹窗之前都内联了同样的 6 个字段 (数据集/基础模型/版本/轮次/批大小/学习率)
    2. 内联版本中基础模型下拉的 :key/:label/:value 写法不一致,
       导致新建弹窗里显示 "resnet18 timm", 再训练弹窗里却显示 JSON 对象
    3. 同一份 UI 散落两处 → 改了 A 忘了 B, 必然再次分叉
  解耦方案:
    - 数据双向绑定 (v-model: form) — 表单只是 form 的"视图", 不持有数据
    - 数据集列表 / 基础模型列表通过 props 注入 — 组件不耦合 datasetApi
    - 内部样式集中维护, 改动一次, 两个弹窗同步生效
    - 基础模型下拉改用 BaseModelSelect 组件, 与 DatasetDetail 的 AI 预标注下拉样式统一
-->
<template>
  <el-form :model="form" label-width="90px" size="default">
    <el-form-item label="数据集" required>
      <el-select
        :model-value="form.dataset_id"
        placeholder="请选择数据集"
        style="width: 100%;"
        @update:model-value="onUpdate('dataset_id', $event)"
      >
        <el-option
          v-for="d in datasets"
          :key="d.id"
          :label="d.name"
          :value="d.id"
        />
      </el-select>
    </el-form-item>

    <!-- 基础模型: 复用 BaseModelSelect 组件, 与 DatasetDetail 样式一致 -->
    <el-form-item label="基础模型">
      <BaseModelSelect
        :model-value="form.base_model"
        :models="baseModels"
        @update:model-value="(v: string) => onUpdate('base_model', v)"
      />
    </el-form-item>

    <el-form-item label="模型版本" required>
      <el-input
        :model-value="form.model_name"
        placeholder="例如: resnet50_v1_1701234567 (含基础模型+版本+时间戳)"
        @update:model-value="onUpdate('model_name', $event)"
      >
        <template #append>
          <el-button
            :icon="Refresh"
            @click="emitAutoName"
            title="基于当前基础模型 + 时间戳自动生成"
          >生成</el-button>
        </template>
      </el-input>
    </el-form-item>

    <el-form-item label="训练轮次">
      <el-input-number
        :model-value="form.epochs"
        :min="1"
        :max="200"
        @update:model-value="onUpdate('epochs', $event)"
      />
    </el-form-item>

    <el-form-item label="批大小">
      <el-input-number
        :model-value="form.batch_size"
        :min="1"
        :max="256"
        @update:model-value="onUpdate('batch_size', $event)"
      />
    </el-form-item>

    <el-form-item label="学习率">
      <el-input-number
        :model-value="form.learning_rate"
        :min="0.00001"
        :max="0.1"
        :step="0.0001"
        :precision="5"
        @update:model-value="onUpdate('learning_rate', $event)"
      />
    </el-form-item>
  </el-form>
</template>

<script setup lang="ts">
/**
 * 表单字段类型 (与父组件的 form 引用保持一致)
 */
import { Refresh } from '@element-plus/icons-vue'
import BaseModelSelect, { type BaseModelOption } from './BaseModelSelect.vue'

export interface TrainingParams {
  dataset_id: number | null
  base_model: string
  model_name: string
  epochs: number
  batch_size: number
  learning_rate: number
}

const props = defineProps<{
  form: TrainingParams
  datasets: Array<{ id: number; name: string }>
  baseModels: BaseModelOption[]
}>()

const emit = defineEmits<{
  'form-change': [patch: Partial<TrainingParams>]
}>()

/** 单字段更新: emit 部分 patch, 父组件 Object.assign 到自己的 form 引用 */
const onUpdate = <K extends keyof TrainingParams>(key: K, value: TrainingParams[K]) => {
  emit('form-change', { [key]: value } as Partial<TrainingParams>)
}

/**
 * 工具: 基于 base_model + 时间戳生成默认 model_name
 * 格式: {base_model}_v{ver}_{ts}  例: resnet50_v1_1701234567
 * 取 base_model 中已有的 _v 之后的部分作为 ver 后缀, 默认 ver=1
 * 与后端 start_training 默认值生成保持一致
 */
const genAutoName = (baseModel: string): string => {
  // 默认版本号 v1, 时间戳取 10 位 (秒级)
  const ts = Math.floor(Date.now() / 1000) % 10000000000
  return `${baseModel}_v1_${ts}`
}

/** 触发自动生成 model_name */
const emitAutoName = () => {
  emit('form-change', { model_name: genAutoName(props.form.base_model) })
}
</script>

<style scoped>
/* 基础模型下拉项样式已迁到 BaseModelSelect 组件, 此处无遗留 */
</style>

