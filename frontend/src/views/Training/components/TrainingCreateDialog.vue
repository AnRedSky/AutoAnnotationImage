<script setup lang="ts">
/**
 * TrainingCreateDialog - 新建训练任务对话框
 *
 * v3.0.0 Phase J 拆分: 从 Training/index.vue 抽离
 * - 任务类型下拉 (固定排序: 分类/检测/分割)
 * - 复用 TrainingParamsForm 子组件
 * - 切换 task_type 时: 重置 base_model + model_name, 联动数据集下拉
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { trainingApi } from '@/api'
import { getDefaultBaseModel } from '@/utils/taskType'
import type { BaseModelOption } from './BaseModelSelect.vue'
import TrainingParamsForm, { type TrainingParams } from './TrainingParamsForm.vue'

const props = defineProps<{
  modelValue: boolean
  datasets: any[]
  baseModelsByTask: Record<string, BaseModelOption[]>
  /**
   * 生成默认 model_name 的函数 (父组件注入)
   * 签名: (baseModel: string, retrain?: boolean) => string
   * 新建场景: retrain=false → `{base}_{ts}`
   * 再训练场景: retrain=true  → `{base}_r_{ts}`
   *
   * v3.5.1 改版: 父组件传 genDefaultModelName, 恢复「生成」按钮 (用户可主动调),
   * 规则与后端 _default_model_name 完全一致 (前端无法做查重, 重名时由后端兜底).
   */
  genDefaultModelName: (baseModel: string, retrain?: boolean) => string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: boolean): void
  (e: 'submit', payload: { params: TrainingParams; newTaskId: string; newJobId?: number }): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const submitting = ref(false)

// 任务类型下拉选项 (固定排序)
const CREATE_TASK_TYPE_OPTIONS = [
  { value: 'classification', label: '图片分类' },
  { value: 'detection',      label: '目标检测' },
  { value: 'segmentation',   label: '图片分割' },
] as const

const taskType = ref<string>('classification')

// 当前 task_type 对应的 base_model 候选
const baseOptions = computed<BaseModelOption[]>(
  () => props.baseModelsByTask[taskType.value] || []
)

const form = ref<TrainingParams>({
  dataset_id: null,
  base_model: 'resnet50',
  model_name: '',
  epochs: 20,
  batch_size: 32,
  learning_rate: 0.0001,
})

// 切换 task_type 时: 重置 base_model, model_name 留空 (后端接管生成)
const resetByTaskType = (t: string) => {
  const opts = props.baseModelsByTask[t] || []
  const newBase = opts[0]?.name || getDefaultBaseModel(t)
  form.value.base_model = newBase
  form.value.model_name = ''
}

const onTaskTypeChange = (t: string) => {
  taskType.value = t
  resetByTaskType(t)
  // 当前 dataset_id 可能不在新筛选范围, 清空强制重选
  const cur = form.value.dataset_id
  if (cur) {
    const stillValid = props.datasets.find(
      (d: any) => d.id === cur && (d.task_type || 'classification') === t
    )
    if (!stillValid) form.value.dataset_id = null
  }
}

const onParamsChange = (_form: TrainingParams, patch: Partial<TrainingParams>) => {
  Object.assign(form.value, patch)
}

// 联动筛选: 任务类型变了, 数据集下拉只显示同 task_type 的数据集
const filteredDatasets = computed(() => {
  return props.datasets.filter(
    (d: any) => (d.task_type || 'classification') === taskType.value
  )
})

// 每次打开重置
watch(visible, (v) => {
  if (v) {
    taskType.value = 'classification'
    // v3.0.0: model_name 默认空, 由后端 _default_model_name 接管生成
    form.value = {
      dataset_id: null,
      base_model: 'resnet50',
      model_name: '',
      epochs: 20,
      batch_size: 32,
      learning_rate: 0.0001,
    }
  }
})

const onSubmit = async () => {
  if (!form.value.dataset_id) {
    ElMessage.warning('请选择数据集')
    return
  }
  // v3.0.0: model_name 留空 = 后端按 _default_model_name(base_model) 生成
  submitting.value = true
  try {
    // model_name 留空时显式传 '', 让后端接管
    const payloadModelName = (form.value.model_name || '').trim()
    const r: any = await trainingApi.start({
      ...form.value,
      model_name: payloadModelName,
      dataset_id: form.value.dataset_id!,
    })
    const newTaskId: string = r.task_id
    const newJobId: number | undefined = r.job_id
    ElMessage.success(`训练任务已提交 (job=#${newJobId ?? '?'})`)
    visible.value = false
    emit('submit', { params: { ...form.value, model_name: payloadModelName }, newTaskId, newJobId })
  } catch (e: any) {
    ElMessage.error('启动失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    title="新建训练任务"
    width="520px"
    destroy-on-close
    :close-on-click-modal="false"
  >
    <el-form label-width="90px" size="default">
      <el-form-item label="任务类型">
        <el-select
          :model-value="taskType"
          class="app-select" style="width: 160px;"
          @update:model-value="onTaskTypeChange"
        >
          <el-option
            v-for="opt in CREATE_TASK_TYPE_OPTIONS"
            :key="opt.value" :value="opt.value" :label="opt.label"
          />
        </el-select>
      </el-form-item>
    </el-form>
    <TrainingParamsForm
      :form="form"
      :datasets="filteredDatasets"
      :base-models="baseOptions"
      :gen-default-model-name="genDefaultModelName"
      @form-change="(p) => onParamsChange(form, p)"
    />
    <el-alert
      title="提示: 训练任务启动后会进入 Celery 队列, 需要 worker 在跑才能真正开始执行"
      type="info" :closable="false" show-icon
      style="margin-top: 16px;"
    />
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button
        type="primary" :loading="submitting"
        @click="onSubmit"
      >提交</el-button>
    </template>
  </el-dialog>
</template>
