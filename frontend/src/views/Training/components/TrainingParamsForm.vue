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

    <!-- 基础模型: 复用 BaseModelSelect 组件, 与 DatasetDetail 样式一致
         hide 模式 (再训练弹窗用): 整个 el-form-item 都不渲染, 由父组件自行展示当前模型
         readonly 模式 (旧, 保留兼容): 只读 el-tag 展示
         注: 必须 v-if/v-else-if/v-else 互斥, 不能共存, 否则 v-if 切换时 el-select 的
             popper 浮层 DOM 残留到 el-form-item 右侧 (产生一列乱码文字) -->
    <el-form-item
      v-if="!hideBaseModel"
      :label="readonlyBaseModel ? '当前模型' : '基础模型'"
    >
      <BaseModelSelect
        v-if="!readonlyBaseModel"
        :key="`base-model-select-${readonlyBaseModel}`"
        :model-value="form.base_model"
        :models="baseModels"
        @update:model-value="(v: string) => onUpdate('base_model', v)"
      />
      <div v-else class="readonly-model">
        <el-tag type="info" effect="plain" size="default">
          {{ currentModelLabel || form.base_model }}
        </el-tag>
        <span class="readonly-model__hint">
          再训练必须保持原骨架, 改用其他网络结构会导致权重加载失败
        </span>
      </div>
    </el-form-item>

    <el-form-item label="模型版本" required>
      <!--
        v3.0.0 改版: 「生成」按钮调用 genDefaultModelName (父组件传),
        生成规则: 新建 `{base_model}_{ts}` / 再训练 `{base_model}_r_{ts}`,
        与后端 _default_model_name 完全一致 (前端无法做查重, 仅生成, 重名时仍由后端兜底).
      -->
      <el-input
        :model-value="form.model_name"
        :maxlength="MODEL_NAME_INPUT_MAX"
        :show-word-limit="true"
        placeholder="留空则后端自动按「{base_model}_{时间戳}」生成 (重名时加 _xxx)"
        @update:model-value="(v: string) => onUpdate('model_name', v)"
      >
        <template #append>
          <el-button
            :icon="Refresh"
            @click="emitAutoName"
            title="基于当前基础模型 + 时间戳自动生成 (新建任务格式: {base}_{ts})"
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
 *
 * v3.0.0 改版: 「生成」按钮调用父组件传的 genDefaultModelName (新建场景 retrain=false),
 * 生成的名称 `{base_model}_{ts}` 与后端 _default_model_name 规则一致, 避免前后端不一致.
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

// 输入框 maxlength: 与后端 TrainingJob.model_name / ModelVersion.name String(128) 对齐
// 预留 8 字符给后端追加的 _r{ts} 后缀 (12 字符) 之前的额外保护, 输入框硬上限 120
// 注: <script setup> 不允许 export, 此常量仅本组件使用, 不到处
const MODEL_NAME_INPUT_MAX = 120

const props = defineProps<{
  form: TrainingParams
  datasets: Array<{ id: number; name: string }>
  baseModels: BaseModelOption[]
  /**
   * 生成默认 model_name 的函数 (父组件注入)
   * 签名: (baseModel: string, retrain?: boolean) => string
   * 新建场景: retrain=false → `{base}_{ts}`
   * 再训练场景: retrain=true  → `{base}_r_{ts}`
   *
   * v3.0.0 改版: 必须由父组件传, 不再用内联 _v1_ 中缀实现
   * (新建弹窗 = TrainingCreateDialog 用 retrain=false 传入)
   * (再训练弹窗因为走单独 TrainingRetrainDialog, 不复用本组件, 所以传不传都行)
   */
  genDefaultModelName: (baseModel: string, retrain?: boolean) => string
  /** 再训练模式: 基础模型只读, 防止改骨架导致权重加载失败 */
  readonlyBaseModel?: boolean
  /** 再训练模式: 当前模型描述, 例 "ModelVersion #10 (基于 resnet50)" */
  currentModelLabel?: string
  /** 隐藏基础模型整行 (再训练弹窗用, 由父组件自行展示) */
  hideBaseModel?: boolean
}>()

const emit = defineEmits<{
  'form-change': [patch: Partial<TrainingParams>]
}>()

/** 单字段更新: emit 部分 patch, 父组件 Object.assign 到自己的 form 引用 */
const onUpdate = <K extends keyof TrainingParams>(key: K, value: TrainingParams[K]) => {
  emit('form-change', { [key]: value } as Partial<TrainingParams>)
}

/**
 * 触发自动生成 model_name (新建任务: retrain=false)
 * 父组件必须传 genDefaultModelName, 否则降级本地实现 (与后端规则保持一致)
 */
const emitAutoName = () => {
  if (props.genDefaultModelName) {
    emit('form-change', { model_name: props.genDefaultModelName(props.form.base_model, false) })
  } else {
    // 降级: 本地实现, 规则与后端 _default_model_name 一致
    const ts = Math.floor(Date.now() / 1000) % 10000000000
    emit('form-change', { model_name: `${props.form.base_model}_${ts}` })
  }
}
</script>

<style scoped>
/* 基础模型下拉项样式已迁到 BaseModelSelect 组件, 此处无遗留 */

/* 只读模式 (再训练): 标签 + 提示文字, 不开放编辑 */
.readonly-model {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}
.readonly-model__hint {
  font-size: 12px;
  color: var(--text-secondary, #909399);
}
</style>

