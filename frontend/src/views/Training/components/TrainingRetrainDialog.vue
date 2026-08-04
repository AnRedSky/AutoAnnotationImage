<script setup lang="ts">
/**
 * TrainingRetrainDialog - 再训练对话框 (修改参数 + 启动)
 *
 * v3.0.0 Phase J 拆分: 从 Training/index.vue 抽离
 * - 预填当前 row 的所有参数
 * - 用户可以微调 (epochs/batch_size/learning_rate/model_name/dataset/base_model)
 * - 保存时直接 POST /start?mode=restart + body (新参数)
 *   原任务记录保持不变, 仅作为新任务的训练参数基底.
 *   这是「再训练」语义: 新建任务, 不修改原任务.
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { trainingApi } from '@/api'
import type { BaseModelOption } from './BaseModelSelect.vue'
import TrainingParamsForm, { type TrainingParams } from './TrainingParamsForm.vue'

const props = defineProps<{
  modelValue: boolean
  row: any  // 原 job 行
  datasets: any[]
  baseModelsByTask: Record<string, BaseModelOption[]>
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

const taskType = ref<string>('classification')
const baseOptions = computed<BaseModelOption[]>(
  () => props.baseModelsByTask[taskType.value]
      || props.baseModelsByTask.classification
      || []
)

const form = ref<TrainingParams & { id: number; state: string }>({
  id: 0,
  state: 'PENDING',
  dataset_id: null,
  base_model: 'resnet50',
  model_name: '',
  epochs: 20,
  batch_size: 32,
  learning_rate: 0.0001,
})

const onParamsChange = (_form: any, patch: Partial<TrainingParams>) => {
  Object.assign(form.value, patch)
}

// 每次打开重置 (由父组件传 row)
watch(visible, (v) => {
  if (v && props.row) {
    form.value = {
      id: props.row.id,
      state: props.row.state,
      dataset_id: props.row.dataset_id,
      base_model: props.row.base_model,
      model_name: props.row.model_name,
      epochs: props.row.epochs,
      batch_size: props.row.batch_size,
      learning_rate: props.row.learning_rate,
    }
    taskType.value = (props.row.task_type as string) || 'classification'
  }
})

const onSubmit = async () => {
  if (!form.value.dataset_id) {
    ElMessage.warning('请选择数据集')
    return
  }
  if (!form.value.model_name) {
    ElMessage.warning('请填写模型版本名')
    return
  }
  submitting.value = true
  const jobId = form.value.id
  try {
    const r: any = await trainingApi.startJob(jobId, 'restart', {
      dataset_id: form.value.dataset_id!,
      base_model: form.value.base_model,
      model_name: form.value.model_name,
      epochs: form.value.epochs,
      batch_size: form.value.batch_size,
      learning_rate: form.value.learning_rate,
    })
    if (r.success) {
      const newJobId: number | undefined = r.new_job_id
      const newTaskId: string | undefined = r.task_id
      ElMessage.success(r.message || `已创建新一轮训练任务 #${newJobId}, 原任务 #${jobId} 保持不变`)
      visible.value = false
      emit('submit', { params: { ...form.value }, newTaskId: newTaskId || '', newJobId })
    } else {
      ElMessage.warning(r.message || '启动失败')
    }
  } catch (e: any) {
    ElMessage.error('操作失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="`再训练任务 #${form.id}`"
    width="520px"
    destroy-on-close
    :close-on-click-modal="false"
  >
    <el-alert
      type="info" :closable="false" style="margin-bottom: 12px;"
      :title="`可在此调整训练参数 (数据集/基础模型/版本名/轮次/批大小/学习率). 新 model_name 会自动加 _r{时间戳} 后缀, 避免覆盖旧 .pth.`"
    />
    <el-alert
      type="warning" :closable="false" style="margin-bottom: 12px;"
      :title="`原任务 #${form.id} 不会被修改, 此处参数仅用于创建新一轮训练任务. 提交后会立即在列表顶部出现新任务 (PENDING).`"
    />
    <el-alert
      :type="row?.model_version_id ? 'success' : 'info'"
      :closable="false" style="margin-bottom: 12px;"
      :title="row?.model_version_id
        ? `将基于当前行 ModelVersion #${row.model_version_id} 继续训练 (无论是否激活)`
        : '当前行未关联 ModelVersion (训练失败或历史任务), 将从头微调 (ImageNet 预训练)'"
    />
    <TrainingParamsForm
      :form="form"
      :datasets="datasets"
      :base-models="baseOptions"
      @form-change="(p) => onParamsChange(form, p)"
    />
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button
        type="primary" :loading="submitting"
        @click="onSubmit"
      >启动新任务</el-button>
    </template>
  </el-dialog>
</template>
