<script setup lang="ts">
/**
 * 团队管理页 - 共享数据集弹窗 (v3.3.2)
 * =====================================
 *
 * 在团队详情页点击「共享数据集」按钮时弹出。
 * 与 DatasetDetail 页的 ShareDatasetDialog 区别:
 *  - 本弹窗用于「团队管理视图」, 团队 ID 是固定的
 *  - 列出当前用户在团队中的 manager 角色校验
 *  - 后端走 GET /api/teams/{id}/shareable-datasets 拉候选列表
 *
 * Props:
 *  - modelValue: boolean
 *  - teamId:     number
 *  - teamName:   string
 *
 * Emits:
 *  - update:modelValue
 *  - shared   共享成功
 */
import { ref, watch, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading, Share } from '@element-plus/icons-vue'
import { teamApi, type ShareableDatasetItem } from '@/api'

const props = defineProps<{
  modelValue: boolean
  teamId: number
  teamName: string
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'shared'): void
}>()

// 状态
const loading = ref(false)
const submitting = ref(false)
const candidates = ref<ShareableDatasetItem[]>([])
const selectedId = ref<number | null>(null)
const confirmText = ref('')

/** 选中的 dataset 对象 (供展示元信息) */
const selectedDs = computed(() =>
  candidates.value.find((d) => d.id === selectedId.value) || null
)

/** 二次确认: 输入数据集名 */
const canSubmit = computed(
  () =>
    !!selectedDs.value &&
    confirmText.value.trim() === selectedDs.value.name
)

watch(
  () => props.modelValue,
  async (open) => {
    if (open) {
      // 打开时加载候选列表
      selectedId.value = null
      confirmText.value = ''
      await loadCandidates()
    }
  }
)

const loadCandidates = async () => {
  loading.value = true
  try {
    const res: any = await teamApi.listShareableDatasets(props.teamId)
    candidates.value = res.items || []
  } catch (e: any) {
    ElMessage.error(
      '加载可共享数据集失败: ' +
        (e?.response?.data?.detail || e?.message)
    )
  } finally {
    loading.value = false
  }
}

const close = () => emit('update:modelValue', false)

const onSubmit = async () => {
  if (!canSubmit.value || !selectedDs.value) return
  // 二次弹窗确认 (防止误操作)
  try {
    await ElMessageBox.confirm(
      `确认将数据集「${selectedDs.value.name}」共享到团队「${props.teamName}」?\n\n` +
        `共享后, 团队下所有「可管理」和「可编辑」成员可对该数据集进行标注操作。\n` +
        `「可阅读」成员仅可查看。`,
      '确认共享',
      { type: 'info', confirmButtonText: '确认共享', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  submitting.value = true
  try {
    await teamApi.shareDataset(selectedDs.value.id, props.teamId)
    ElMessage.success(
      `「${selectedDs.value.name}」已成功共享到团队「${props.teamName}」`
    )
    emit('shared')
    close()
  } catch (e: any) {
    ElMessage.error(
      '共享失败: ' + (e?.response?.data?.detail || e?.message)
    )
  } finally {
    submitting.value = false
  }
}

const taskTypeLabel = (t: string) => {
  const m: Record<string, string> = {
    classification: '分类',
    detection: '检测',
    segmentation: '分割',
  }
  return m[t] || t
}

const progressPct = (annotated: number, total: number) => {
  if (total === 0) return 0
  return Math.round((annotated / total) * 100)
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    width="640px"
    :close-on-click-modal="false"
    @close="close"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <template #header>
      <div class="dialog-header">
        <el-icon class="dialog-header__icon"><Share /></el-icon>
        <span class="dialog-header__title">共享数据集到「{{ teamName }}」</span>
      </div>
    </template>

    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px">
      <template #title>
        <strong>使用说明</strong>
      </template>
      下方列表展示您拥有的、且未共享给任何团队的数据集。
      选择目标数据集后, 输入数据集名称以二次确认, 即可将其共享至当前团队。
      共享后您可随时在此页面「取消共享」, 或在「数据集详情」中操作。
    </el-alert>

    <!-- 候选数据集列表 -->
    <div class="section-title">选择数据集</div>
    <div v-loading="loading" class="candidate-list">
      <el-empty
        v-if="!loading && candidates.length === 0"
        description="暂无可共享的数据集 (您需为数据集的所有者, 且数据集未共享给任何团队)"
        :image-size="80"
      />
      <div
        v-for="ds in candidates"
        :key="ds.id"
        class="candidate-item"
        :class="{ active: selectedId === ds.id }"
        @click="selectedId = ds.id"
      >
        <el-radio v-model="selectedId" :value="ds.id" class="candidate-radio">
          <div class="candidate-content">
            <div class="candidate-row1">
              <span class="candidate-name">{{ ds.name }}</span>
              <el-tag size="small" effect="plain">{{ taskTypeLabel(ds.task_type) }}</el-tag>
            </div>
            <div class="candidate-row2">
              <span>图片 {{ ds.image_count }}</span>
              <span>已标 {{ ds.annotated_count }} ({{ progressPct(ds.annotated_count, ds.image_count) }}%)</span>
              <span>类别 {{ ds.category_count }}</span>
            </div>
          </div>
        </el-radio>
      </div>
    </div>

    <!-- 选中详情 + 二次确认 -->
    <template v-if="selectedDs">
      <el-divider />
      <el-descriptions :column="2" size="small" border>
        <el-descriptions-item label="数据集 ID">{{ selectedDs.id }}</el-descriptions-item>
        <el-descriptions-item label="任务类型">{{ taskTypeLabel(selectedDs.task_type) }}</el-descriptions-item>
        <el-descriptions-item label="图片数">{{ selectedDs.image_count }}</el-descriptions-item>
        <el-descriptions-item label="已标注">{{ selectedDs.annotated_count }} / {{ selectedDs.image_count }}</el-descriptions-item>
      </el-descriptions>

      <el-form label-position="top" style="margin-top: 12px">
        <el-form-item label="二次确认">
          <p class="confirm-hint">
            请输入数据集全名 <code>{{ selectedDs.name }}</code> 以确认操作:
          </p>
          <el-input
            v-model="confirmText"
            :placeholder="selectedDs.name"
            clearable
          />
        </el-form-item>
      </el-form>
    </template>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button
        type="primary"
        :icon="Loading"
        :disabled="!canSubmit"
        :loading="submitting"
        @click="onSubmit"
      >
        确认共享
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
@import '@/styles/admin.css';
.dialog-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}
.dialog-header__icon {
  color: var(--brand-primary, #409eff);
  font-size: 18px;
}
.section-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 8px;
}
.candidate-list {
  max-height: 360px;
  overflow-y: auto;
  border: 1px solid var(--border-color, #ebeef5);
  border-radius: 6px;
  padding: 4px;
}
.candidate-item {
  padding: 10px 12px;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.15s;
  margin-bottom: 4px;
}
.candidate-item:last-child {
  margin-bottom: 0;
}
.candidate-item:hover {
  background: var(--bg-hover, #f5f7fa);
}
.candidate-item.active {
  background: var(--brand-primary-light, #ecf5ff);
  border: 1px solid var(--brand-primary, #409eff);
}
.candidate-radio {
  width: 100%;
}
.candidate-radio :deep(.el-radio__label) {
  width: 100%;
  padding-left: 8px;
}
.candidate-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
}
.candidate-row1 {
  display: flex;
  align-items: center;
  gap: 8px;
}
.candidate-name {
  font-weight: 500;
  color: var(--text-primary);
}
.candidate-row2 {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: var(--text-secondary);
}
.confirm-hint {
  margin: 0 0 8px 0;
  font-size: 13px;
  color: var(--text-secondary);
}
.confirm-hint code {
  background: var(--bg-tertiary);
  padding: 2px 6px;
  border-radius: 3px;
  font-weight: 600;
  color: var(--color-warning);
}
</style>
