<script setup lang="ts">
/**
 * TrainingRetrainDialog - 再训练对话框 (修改参数 + 启动)
 *
 * v3.0.0 Phase J 拆分: 从 Training/index.vue 抽离
 * v3.0.0 重构: 不再复用 TrainingParamsForm, 自渲染 el-form
 *   - 避免 BaseModelSelect 的 el-select popper 浮层在 v-if 切换时残留到 el-form-item 右侧
 *   - 把 3 条 alert 合并为 1 条, 减少视觉噪音
 *   - 「当前模型」展示: 不可改的 el-tag (改 base_model 会破坏增量训练)
 * v3.0.0 命名: 移除前端「生成」按钮, model_name 留空由后端 _default_model_name 生成
 *
 * 保存时直接 POST /start?mode=restart + body (新参数)
 * 原任务记录保持不变, 仅作为新任务的训练参数基底.
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { trainingApi } from '@/api'

interface TrainingForm {
  id: number
  state: string
  dataset_id: number | null
  base_model: string
  model_name: string
  epochs: number
  batch_size: number
  learning_rate: number
}

const props = defineProps<{
  modelValue: boolean
  row: any  // 原 job 行
  datasets: any[]
  // 再训练不再用基础模型选择, 保留 prop 仅作向后兼容 (父组件可能仍传, 不报错)
  baseModelsByTask?: Record<string, any>
  /**
   * 生成默认 model_name 的函数 (父组件注入)
   * 签名: (baseModel: string, retrain?: boolean) => string
   * 再训练场景: retrain=true → `{base}_r_{ts}`
   *
   * v3.5.1 改版: 父组件传 genDefaultModelName, 恢复「生成」按钮 (用户可主动调),
   * 规则与后端 _default_model_name 完全一致 (前端无法做查重, 重名时由后端兜底).
   */
  genDefaultModelName?: (baseModel: string, retrain?: boolean) => string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: boolean): void
  (e: 'submit', payload: { params: TrainingForm; newTaskId: string; newJobId?: number }): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const submitting = ref(false)

const form = ref<TrainingForm>({
  id: 0,
  state: 'PENDING',
  dataset_id: null,
  base_model: 'resnet50',
  model_name: '',
  epochs: 20,
  batch_size: 32,
  learning_rate: 0.0001,
})

const onParamsChange = (patch: Partial<TrainingForm>) => {
  Object.assign(form.value, patch)
}

/**
 * 「生成」按钮: 调用父组件传的 genDefaultModelName (再训练场景: retrain=true)
 * 生成规则 `{base_model}_r_{timestamp}` 与后端 _default_model_name 完全一致.
 * 降级: 父组件未传时本地拼接 (规则一致, 仅生成, 重名时仍由后端兜底).
 */
const emitAutoName = () => {
  if (props.genDefaultModelName) {
    onParamsChange({ model_name: props.genDefaultModelName(form.value.base_model, true) })
  } else {
    // 降级: 本地实现, 规则与后端 _default_model_name 一致
    const ts = Math.floor(Date.now() / 1000) % 10000000000
    onParamsChange({ model_name: `${form.value.base_model}_r_${ts}` })
  }
}

/**
 * 「当前模型」只读展示 — 改骨架会破坏增量训练, 故锁死
 * - 优先: ModelVersion #X (name) · 基于 {base_model}
 * - 回退: 无关联 ModelVersion (从头微调)
 */
const currentModelLabel = computed(() => {
  const mvId = props.row?.model_version_id
  const base = form.value.base_model
  if (mvId) {
    const mvName = props.row?.model_version_name || props.row?.model_name
    return mvName
      ? ` #${mvId} (${mvName}) · 基于 ${base}`
      : ` #${mvId} · 基于 ${base}`
  }
  return `无关联 (从头微调 ImageNet)`
})

/**
 * 顶部单条 alert 文案 — 把"基础模型说明 / 原任务保留 / 当前 MV"三件事合并
 *
 * v3.0.0: 后端 restart 时强制按 `{base_model}_r_{ts}` 重置 model_name (不累加 _r 后缀),
 *         重名时自动追加 3 位数字. 前端不拼接时间戳, 提交时后端统一处理.
 */
const summaryText = computed(() => {
  if (props.row?.model_version_id) {
    return `将基于 ModelVersion #${props.row.model_version_id} 继续训练。原任务 #${form.value.id} 保持不变，model_name 留空时后端自动按「${form.value.base_model}_r_{时间戳}」生成（重名时自动加随机后缀）。`
  }
  return `当前行未关联 ModelVersion，将从头微调。原任务 #${form.value.id} 保持不变。`
})

// 每次打开重置 (由父组件传 row)
watch(visible, (v) => {
  if (v && props.row) {
    form.value = {
      id: props.row.id,
      state: props.row.state,
      dataset_id: props.row.dataset_id,
      base_model: props.row.base_model,
      // v3.0.0: model_name 默认空, 后端按 _default_model_name(retrain=True) 重置
      // 留空即让后端接管, 避免前端硬编码的旧名与后端规则冲突
      model_name: '',
      epochs: props.row.epochs,
      batch_size: props.row.batch_size,
      learning_rate: props.row.learning_rate,
    }
  }
})

const onSubmit = async () => {
  if (!form.value.dataset_id) {
    ElMessage.warning('请选择数据集')
    return
  }
  // v3.0.0: model_name 留空 = 后端按 _default_model_name(retrain=True) 重置
  // 之前校验 model_name 非空已去掉, 后端会接管命名
  submitting.value = true
  const jobId = form.value.id
  try {
    // model_name 留空时显式传 '', 让后端走 _default_model_name
    const payloadModelName = (form.value.model_name || '').trim()
    const r: any = await trainingApi.startJob(jobId, 'restart', {
      dataset_id: form.value.dataset_id!,
      base_model: form.value.base_model,
      model_name: payloadModelName,
      epochs: form.value.epochs,
      batch_size: form.value.batch_size,
      learning_rate: form.value.learning_rate,
    })
    if (r.success) {
      const newJobId: number | undefined = r.new_job_id
      const newTaskId: string | undefined = r.task_id
      ElMessage.success(r.message || `已创建新一轮训练任务 #${newJobId}, 原任务 #${jobId} 保持不变`)
      visible.value = false
      emit('submit', { params: { ...form.value, model_name: payloadModelName }, newTaskId: newTaskId || '', newJobId })
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
    <!-- 单条 alert: 把"基础模型说明 / 原任务保留 / 当前 MV"三件事合并, 减少视觉噪音 -->
    <el-alert
      :type="row?.model_version_id ? 'success' : 'info'"
      :closable="false" style="margin-bottom: 16px;"
      :title="summaryText"
      show-icon
    />

    <el-form :model="form" label-width="84px" size="default" class="retrain-form">
      <el-form-item label="当前模型">
        <el-tag type="info" effect="plain" size="default">
          {{ currentModelLabel }}
        </el-tag>
      </el-form-item>
      <el-form-item label="数据集" required>
        <el-select
          :model-value="form.dataset_id"
          placeholder="请选择数据集"
          style="width: 100%;"
          @update:model-value="(v: number) => onParamsChange({ dataset_id: v })"
        >
          <el-option
            v-for="d in datasets"
            :key="d.id"
            :label="d.name"
            :value="d.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="模型版本">
        <!--
          v3.5.1 改版: 恢复「生成」按钮, 调用 genDefaultModelName (父组件传),
          生成规则: 再训练 `{base_model}_r_{ts}` 与后端 _default_model_name 一致.
          前端无 DB 查重能力, 重名时仍由后端兜底追加 3 位随机数.
        -->
        <el-input
          :model-value="form.model_name"
          :maxlength="120"
          :show-word-limit="true"
          placeholder="留空则后端自动重置为「{base_model}_r_{时间戳}」(重名时加 _xxx)"
          @update:model-value="(v: string) => onParamsChange({ model_name: v })"
        >
          <template #append>
            <el-button
              :icon="Refresh"
              @click="emitAutoName"
              title="基于当前基础模型 + 时间戳自动生成 (再训练格式: {base}_r_{ts})"
            >生成</el-button>
          </template>
        </el-input>
      </el-form-item>
      <el-form-item label="训练轮次">
        <el-input-number
          :model-value="form.epochs"
          :min="1" :max="200"
          @update:model-value="(v: number) => onParamsChange({ epochs: v })"
        />
      </el-form-item>
      <el-form-item label="批大小">
        <el-input-number
          :model-value="form.batch_size"
          :min="1" :max="256"
          @update:model-value="(v: number) => onParamsChange({ batch_size: v })"
        />
      </el-form-item>
      <el-form-item label="学习率">
        <el-input-number
          :model-value="form.learning_rate"
          :min="0.00001" :max="0.1" :step="0.0001" :precision="5"
          @update:model-value="(v: number) => onParamsChange({ learning_rate: v })"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button
        type="primary" :loading="submitting"
        @click="onSubmit"
      >启动新任务</el-button>
    </template>
  </el-dialog>
</template>
