<script setup lang="ts">
/**
 * AnnotationViewer - 单图标注查看/编辑组件
 * - 大图预览
 * - AI Top-5 候选 (带置信度条)
 * - 当前最终类别
 * - 标注历史 (谁、何时、耗时、动作)
 * - 确认/修正/强制采用按钮
 */
import { ref, watch, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, Close, Position, Clock, User } from '@element-plus/icons-vue'
import { imageApi, annotationApi, datasetApi } from '@/api'

const props = defineProps<{
  imageId: number
  /** 是否允许编辑（编辑模式显示 confirm/correct 按钮） */
  editable?: boolean
  /** 可选：仅展示模式（不显示操作按钮） */
  readonly?: boolean
}>()

const emit = defineEmits<{
  saved: [info: { label_id: number; label_name: string; is_confirm: boolean }]
  closed: []
}>()

const loading = ref(false)
const detail = ref<any>(null)
const categories = ref<any[]>([])
const startTs = ref(0)
const submitting = ref(false)
const newLabelId = ref<number | null>(null)
const overrideStart = ref(0)  // 强制覆盖计时起点

async function load() {
  if (!props.imageId) return
  loading.value = true
  try {
    const d: any = await imageApi.detail(props.imageId)
    detail.value = d
    // 取类别
    if (d.dataset_id) {
      const c: any = await datasetApi.categories(d.dataset_id)
      categories.value = c?.items || c || []
    }
    startTs.value = Date.now()
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

watch(() => props.imageId, (v) => { if (v) load() }, { immediate: true })

onMounted(load)

async function pickLabel(labelId: number, labelName: string, isConfirm: boolean) {
  if (submitting.value || !detail.value) return
  submitting.value = true
  const cost = Date.now() - (overrideStart.value || startTs.value)
  try {
    await annotationApi.save({
      image_id: detail.value.id,
      label_id: labelId,
      time_spent_ms: cost,
      is_confirm: isConfirm
    })
    ElMessage.success(
      `${isConfirm ? '确认' : '修正'}「${labelName}」成功, 耗时 ${cost}ms`
    )
    emit('saved', { label_id: labelId, label_name: labelName, is_confirm: isConfirm })
    await load()  // 刷新详情 + 历史
    overrideStart.value = 0
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    submitting.value = false
  }
}

function onPickOther(id: number) {
  if (!id) return
  const cat = categories.value.find((c) => c.id === id)
  if (cat) {
    overrideStart.value = Date.now()
    pickLabel(cat.id, cat.name, false)
  }
}

const aiTop5 = computed(() => {
  const pred = detail.value?.ai_prediction
  if (!pred || !Array.isArray(pred.top5)) return []
  // 统一归一化: 标签不在项目 category 里 (=AI 预训练模型 ImageNet 输出)
  // 一律归为「未知」, 保留原置信度供审计
  return pred.top5.map((c: any) => {
    const registered = categories.value.some((x) => x.name === c.label)
    return {
      ...c,
      displayLabel: registered ? c.label : '未知',
      registered,
    }
  })
})

// 关键判断: 全部 Top-5 都不在项目 category 里 → 基础模型输出
// 整组归一为「未知」一个提示框, 不再分 5 个 + 各自置信度
const allUnknown = computed(() => {
  if (!aiTop5.value.length) return false
  return aiTop5.value.every((c: { registered: boolean }) => !c.registered)
})

function labelNameOf(top1: string): { id: number | null; name: string; registered: boolean } {
  const c = categories.value.find((x) => x.name === top1)
  return c ? { id: c.id, name: top1, registered: true } : { id: null, name: top1, registered: false }
}

function statusBadgeType(status: string): 'success' | 'warning' | 'info' | 'primary' | 'danger' {
  const t: Record<string, 'success' | 'warning' | 'info' | 'primary' | 'danger'> = {
    pending: 'info',
    ai_labeled: 'primary',
    human_confirmed: 'success',
    human_corrected: 'warning',
    trained: 'success'
  }
  return t[status] || 'info'
}

function statusLabel(status: string): string {
  return {
    pending: '待标注',
    ai_labeled: 'AI 预标注',
    human_confirmed: '已确认',
    human_corrected: '已修正',
    trained: '已训练'
  }[status] || status
}

function confColor(c: number): string {
  if (c >= 0.8) return '#67c23a'
  if (c >= 0.5) return '#e6a23c'
  return '#909399'
}

function actionLabel(a: string): string {
  return { confirm: '确认', correct: '修正', ai_predict: 'AI 预测', reject: '驳回' }[a] || a
}

function actionType(a: string): 'success' | 'warning' | 'info' | 'danger' {
  return { confirm: 'success', correct: 'warning', ai_predict: 'info', reject: 'danger' }[a] as any || 'info'
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return '-'
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  } catch { return iso }
}

function formatCost(ms: number): string {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}
</script>

<template>
  <div v-loading="loading" class="ann-viewer">
    <div v-if="detail" class="ann-viewer-body">
      <!-- 左：图片 -->
      <div class="ann-viewer-left">
        <div class="image-frame">
          <img :src="imageApi.fileUrl(detail.id)" :alt="detail.filename" />
        </div>
        <div class="image-meta">
          <div><strong>文件名:</strong> {{ detail.filename }}</div>
          <div>
            <strong>尺寸:</strong> {{ detail.width }}×{{ detail.height }} ·
            <strong>大小:</strong> {{ ((detail.file_size || 0) / 1024).toFixed(1) }} KB
          </div>
          <div>
            <strong>状态:</strong>
            <el-tag :type="statusBadgeType(detail.status)" size="small" style="margin-left: 4px;">
              {{ statusLabel(detail.status) }}
            </el-tag>
          </div>
          <div v-if="detail.annotated_at">
            <strong>标注时间:</strong> {{ formatTime(detail.annotated_at) }}
          </div>
        </div>
      </div>

      <!-- 右：标注信息 -->
      <div class="ann-viewer-right">
        <!-- 最终类别 -->
        <el-card shadow="never" header="最终类别" class="block">
          <div v-if="detail.final_label">
            <el-tag :color="detail.final_label.color" effect="dark" size="large">
              {{ detail.final_label.name }}
            </el-tag>
          </div>
          <el-empty v-else description="尚未标注" :image-size="60" />
          <template v-if="!readonly && editable !== false">
            <el-divider>选择其他类别（修正）</el-divider>
            <el-select
              v-model="newLabelId" placeholder="选择其他类别" style="width: 100%;"
              filterable
              @change="onPickOther"
            >
              <el-option
                v-for="c in categories" :key="c.id" :label="c.name" :value="c.id"
              />
            </el-select>
          </template>
        </el-card>

        <!-- AI Top-5 候选 -->
        <el-card shadow="never" header="AI 候选标签（Top-5）" class="block">
          <el-empty v-if="aiTop5.length === 0" description="该图无 AI 预测" :image-size="60" />
          <!-- 关键简化: 基础模型输出 = 全部 Top-5 都不在项目类目
               → 整组归一为「未知」一个提示框, 不再分 5 个 + 各自置信度 -->
          <div v-else-if="allUnknown"
            style="text-align: center; padding: 28px 12px; border: 1px dashed #f56c6c; border-radius: 6px; background: #fef0f0;">
            <el-tag type="danger" size="large" effect="dark">未知</el-tag>
            <div style="color: #f56c6c; font-size: 13px; margin-top: 12px; line-height: 1.6;">
              AI 基础模型标注信息不在项目类别内<br />
              统一归类为「未知」, 请人工选择正确类别
            </div>
          </div>
          <div v-for="(c, idx) in aiTop5" v-show="!allUnknown" :key="`${c.label}-${idx}`" class="top5-item">
            <div class="top5-row">
              <span>
                <el-tag size="small" type="info" style="margin-right: 6px;">#{{ idx + 1 }}</el-tag>
                <!-- 统一归一: 预训练模型输出 (不在项目类目) → 显示「未知」红斜体 -->
                <strong :class="{ 'unknown-label': !c.registered }">{{ c.displayLabel }}</strong>
              </span>
              <el-tag :color="confColor(c.confidence)" effect="dark">
                {{ (c.confidence * 100).toFixed(1) }}%
              </el-tag>
            </div>
            <el-progress
              :percentage="Math.round(c.confidence * 100)"
              :color="confColor(c.confidence)"
              :show-text="false" :stroke-width="6"
              style="margin: 2px 0;"
            />
            <div v-if="!readonly && editable !== false" class="top5-actions">
              <template v-if="c.registered">
                <el-button size="small" type="primary" :icon="Check" :loading="submitting"
                  @click="pickLabel(labelNameOf(c.label).id!, c.label, true)">
                  确认
                </el-button>
                <el-button size="small" :icon="Position" :loading="submitting"
                  @click="pickLabel(labelNameOf(c.label).id!, c.label, false)">
                  强制采用
                </el-button>
              </template>
              <!-- 统一归一: 预训练模型标签禁止采纳, 强制人工从下拉框选 -->
              <el-tag v-else type="danger" size="small">
                未知（AI 预训练模型输出, 禁止采纳）
              </el-tag>
            </div>
          </div>
        </el-card>

        <!-- 标注历史 -->
        <el-card shadow="never" header="标注历史" class="block">
          <el-empty v-if="!detail.annotation_history || detail.annotation_history.length === 0"
            description="暂无标注操作" :image-size="60" />
          <el-timeline v-else>
            <el-timeline-item
              v-for="log in detail.annotation_history" :key="log.id"
              :type="actionType(log.action)" :timestamp="formatTime(log.created_at)"
            >
              <el-tag :type="actionType(log.action)" size="small" effect="plain">
                {{ actionLabel(log.action) }}
              </el-tag>
              <span v-if="log.from_label && log.to_label" style="margin-left: 8px;">
                <el-tag size="small">{{ log.from_label.name }}</el-tag>
                →
                <el-tag size="small" type="primary">{{ log.to_label.name }}</el-tag>
              </span>
              <span v-else-if="log.to_label" style="margin-left: 8px;">
                → <el-tag size="small" type="primary">{{ log.to_label.name }}</el-tag>
              </span>
              <div v-if="log.time_spent_ms" style="margin-top: 2px; color: #909399; font-size: 12px;">
                <el-icon><Clock /></el-icon> 耗时 {{ formatCost(log.time_spent_ms) }}
              </div>
            </el-timeline-item>
          </el-timeline>
        </el-card>
      </div>
    </div>
    <el-empty v-else-if="!loading" description="未选择图片" />
  </div>
</template>

<style scoped>
.ann-viewer { min-height: 200px; }
.ann-viewer-body {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
.ann-viewer-left {
  flex: 0 0 460px;
  max-width: 460px;
}
.image-frame {
  background: #000;
  border-radius: 4px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 360px;
  max-height: 480px;
}
.image-frame img {
  max-width: 100%;
  max-height: 480px;
  display: block;
}
.image-meta {
  margin-top: 12px;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 13px;
  color: #606266;
  line-height: 1.8;
}
.ann-viewer-right {
  flex: 1;
  min-width: 0;
}
.block { margin-bottom: 12px; }
.top5-item {
  padding: 10px 0;
  border-bottom: 1px dashed #ebeef5;
}
.top5-item:last-child { border-bottom: none; }
.top5-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.top5-actions {
  margin-top: 6px;
  display: flex;
  gap: 6px;
}
/* 统一归一: 预训练模型输出 → 红斜体 */
.unknown-label {
  color: #f56c6c;
  font-style: italic;
  font-weight: 600;
}
</style>
