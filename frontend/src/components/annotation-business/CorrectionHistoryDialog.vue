<!--
  CorrectionHistoryDialog.vue (v3.4.0)
  ====================================================
  单图完整修正历史弹窗 (含 diff + 恢复 AI 预测)
  路径: src/components/annotation-business/CorrectionHistoryDialog.vue
  (业务通用层, 因为数据来自 annotationApi.correctionHistory/revertToAi)

  - 展示当前状态 + AI 预测 top1 + 当前 final_label
  - 展示所有 confirm / correct / revert_to_ai 日志, 按时间正序
  - 每条日志:
    * action → 中文标签 + 颜色
    * 类别流转 (from_label → to_label)
    * 耗时 / 操作人 / 时间
    * diff (仅 correct): from.ai_top1 / from.label_id → to.label_id
    * comment (用户填的修正原因)
  - 「恢复 AI 预测」按钮: 当 image.status ∈ {human_confirmed, human_corrected}
    且有 ai_prediction 时, 调 annotationApi.revertToAi
  - v-model 双向绑定 visible
  - emit('reverted'): 成功恢复后通知父组件刷新

  使用:
    <CorrectionHistoryDialog
      v-model="visible"
      :image-id="imageId"
      @reverted="onReverted"
    />
-->
<template>
  <el-dialog
    :model-value="modelValue"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
    title="修正历史"
    width="760px"
    :close-on-click-modal="false"
    destroy-on-close
    @open="onOpen"
    @close="onClose"
  >
    <div v-loading="loading">
      <!-- 当前状态 + AI 对比 -->
      <el-card v-if="data" shadow="never" class="current-block">
        <div class="current-row">
          <span class="current-label">当前状态:</span>
          <el-tag :type="statusTagType(data.current_status)" size="small">
            {{ statusLabel(data.current_status) }}
          </el-tag>
        </div>
        <div class="current-row">
          <span class="current-label">AI 预测 top1:</span>
          <el-tag v-if="data.ai_prediction?.top1" type="info" size="small">
            {{ data.ai_prediction.top1 }}
            <span v-if="data.ai_prediction.top1_conf != null" style="margin-left: 4px;">
              ({{ (data.ai_prediction.top1_conf * 100).toFixed(1) }}%)
            </span>
          </el-tag>
          <span v-else style="color: #909399; font-size: 13px;">无 AI 预测</span>
        </div>
        <div class="current-row">
          <span class="current-label">当前人工标注:</span>
          <el-tag v-if="data.current_label_name" type="primary" size="small">
            {{ data.current_label_name }}
          </el-tag>
          <span v-else style="color: #909399; font-size: 13px;">未标注</span>
          <!-- v3.4.0: 复用 CorrectionDiffBadge 做差异展示 -->
          <CorrectionDiffBadge
            v-if="data.ai_prediction?.top1 || data.current_label_name"
            style="margin-left: 8px;"
            :ai-top1="data.ai_prediction?.top1 || null"
            :current-label-name="data.current_label_name || null"
          />
        </div>
      </el-card>

      <!-- 操作栏: 恢复 AI 预测 -->
      <div v-if="data && canRevert" class="action-bar">
        <el-alert
          type="warning" :closable="false" show-icon
          title="该图当前为人工确认 / 修正状态, 可恢复到 AI 预标注 (会清空 final_label, 保留 ai_prediction)"
        />
        <el-button
          type="warning" plain :icon="RefreshLeft" :loading="reverting"
          @click="onRevert"
        >
          恢复 AI 预测
        </el-button>
      </div>
      <div v-else-if="data && data.current_status === 'trained'" class="action-bar">
        <el-alert
          type="info" :closable="false" show-icon
          title="该图已用于训练 (status=trained), 为避免破坏训练快照, 禁止恢复 AI 预测"
        />
      </div>

      <!-- 时间线: 历史记录 -->
      <el-divider v-if="data">历史记录 ({{ data.items?.length || 0 }})</el-divider>
      <el-empty v-if="data && (!data.items || data.items.length === 0)"
        description="该图暂无标注/修正记录" :image-size="60" />
      <el-timeline v-else-if="data">
        <el-timeline-item
          v-for="log in data.items" :key="log.id"
          :type="actionType(log.action)" :timestamp="formatTime(log.created_at)"
          :hollow="log.action === 'revert_to_ai'"
        >
          <div class="log-line">
            <el-tag :type="actionType(log.action)" size="small" effect="plain">
              {{ actionLabel(log.action) }}
            </el-tag>
            <span class="log-username">
              <el-icon style="vertical-align: -2px;"><User /></el-icon>
              {{ log.username }}
            </span>
            <span v-if="log.time_spent_ms" class="log-cost">
              <el-icon style="vertical-align: -2px;"><Clock /></el-icon>
              {{ formatCost(log.time_spent_ms) }}
            </span>
          </div>

          <!-- 类别流转 (from → to) -->
          <div v-if="log.from_label_name || log.to_label_name" class="log-flow">
            <template v-if="log.from_label_name && log.to_label_name">
              <el-tag size="small">{{ log.from_label_name }}</el-tag>
              <el-icon style="margin: 0 6px; color: #909399;"><ArrowRight /></el-icon>
              <el-tag size="small" type="primary">{{ log.to_label_name }}</el-tag>
            </template>
            <template v-else-if="log.to_label_name">
              → <el-tag size="small" type="primary">{{ log.to_label_name }}</el-tag>
            </template>
            <template v-else-if="log.from_label_name">
              <el-tag size="small">{{ log.from_label_name }}</el-tag>
              → 清空
            </template>
          </div>

          <!-- diff (仅 correct) -->
          <div v-if="log.payload?.diff" class="log-diff">
            <el-icon style="vertical-align: -2px; color: #909399;"><InfoFilled /></el-icon>
            <span class="diff-text">
              <span v-if="log.payload.diff.from?.ai_top1">
                AI: <strong>{{ log.payload.diff.from.ai_top1 }}</strong>
                <template v-if="log.payload.diff.from.ai_top1_conf != null">
                  ({{ (log.payload.diff.from.ai_top1_conf * 100).toFixed(1) }}%)
                </template>
              </span>
              <span v-if="log.payload.diff.from?.ai_top1 && log.payload.diff.to?.label_id" class="diff-arrow">→</span>
              <span v-if="log.payload.diff.to?.label_id">
                最终: <strong>{{ getLogToName(log) }}</strong>
              </span>
            </span>
          </div>

          <!-- 修正原因 (comment) -->
          <div v-if="log.payload?.comment" class="log-comment">
            <el-icon style="vertical-align: -2px; color: #e6a23c;"><ChatLineRound /></el-icon>
            <span>{{ log.payload.comment }}</span>
          </div>
        </el-timeline-item>
      </el-timeline>
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * CorrectionHistoryDialog - 单图完整修正历史弹窗
 *
 * 数据来源: GET /api/annotations/correction-history/{image_id}
 * 恢复操作: POST /api/annotations/revert-to-ai/{image_id}
 *
 * 业务规则:
 * - 恢复 AI 预测仅当 status ∈ {human_confirmed, human_corrected} 可用
 * - status=trained 时禁止恢复 (避免破坏训练快照)
 * - 无 ai_prediction 时不允许恢复
 */
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  RefreshLeft, User, Clock, ArrowRight, InfoFilled, ChatLineRound,
} from '@element-plus/icons-vue'
import { annotationApi } from '@/api'
// v3.4.0: 复用 common 层的 diff 徽章
import CorrectionDiffBadge from '@/components/common/CorrectionDiffBadge.vue'

interface HistoryItem {
  id: number
  action: 'confirm' | 'correct' | 'revert_to_ai'
  username: string
  from_label_id: number | null
  from_label_name: string | null
  to_label_id: number | null
  to_label_name: string | null
  time_spent_ms: number
  created_at: string
  payload?: {
    diff?: {
      from?: { label_id?: number | null; ai_top1?: string | null; ai_top1_conf?: number | null }
      to?: { label_id?: number }
    }
    comment?: string
    from_log_id?: number
    reason?: string
    ai_top1?: string
  } | null
}

interface HistoryData {
  image_id: number
  current_status: string
  current_label_id: number | null
  current_label_name: string | null
  ai_prediction: { top1?: string; top5?: any[]; top1_conf?: number } | null
  items: HistoryItem[]
}

const props = defineProps<{
  modelValue: boolean
  imageId: number
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'reverted'): void  // 成功恢复后, 父组件可刷新详情
}>()

const loading = ref(false)
const reverting = ref(false)
const data = ref<HistoryData | null>(null)

const canRevert = computed(() => {
  if (!data.value) return false
  if (data.value.current_status === 'trained') return false
  if (data.value.current_status !== 'human_confirmed'
      && data.value.current_status !== 'human_corrected') return false
  return !!data.value.ai_prediction
})

async function onOpen() {
  if (!props.imageId) return
  loading.value = true
  try {
    const r: any = await annotationApi.correctionHistory(props.imageId)
    data.value = r
  } catch (e: any) {
    ElMessage.error('加载修正历史失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

function onClose() {
  // 关闭时清空, 避免下次打开时闪烁旧数据
  data.value = null
}

async function onRevert() {
  if (!props.imageId) return
  try {
    await ElMessageBox.confirm(
      '确认恢复到 AI 预标注? 此操作会清空当前人工标注 (final_label), 保留 AI 预测快照, 状态回到「AI 预标注」。',
      '恢复 AI 预测',
      {
        type: 'warning',
        confirmButtonText: '恢复',
        cancelButtonText: '取消',
      }
    )
  } catch {
    return  // 用户取消
  }

  reverting.value = true
  try {
    const r: any = await annotationApi.revertToAi(props.imageId)
    ElMessage.success(`已恢复 AI 预测 (${r?.ai_top1 || '无 top1'})`)
    emit('reverted')
    // 重新拉数据
    await onOpen()
  } catch (e: any) {
    ElMessage.error('恢复失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    reverting.value = false
  }
}

// ============== 辅助函数 ==============

function statusLabel(s: string): string {
  return ({
    pending: '待标注',
    ai_labeled: 'AI 预标注',
    human_confirmed: '已确认',
    human_corrected: '已修正',
    trained: '已训练',
  } as Record<string, string>)[s] || s
}

function statusTagType(s: string): 'success' | 'warning' | 'info' | 'primary' | 'danger' {
  return (({
    pending: 'info',
    ai_labeled: 'primary',
    human_confirmed: 'success',
    human_corrected: 'warning',
    trained: 'success',
  } as Record<string, 'success' | 'warning' | 'info' | 'primary' | 'danger'>)[s]) || 'info'
}

function actionLabel(a: string): string {
  return ({
    confirm: '确认',
    correct: '修正',
    revert_to_ai: '恢复 AI 预测',
  } as Record<string, string>)[a] || a
}

function actionType(a: string): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  return (({
    confirm: 'success',
    correct: 'warning',
    revert_to_ai: 'info',
  } as Record<string, 'primary' | 'success' | 'warning' | 'info' | 'danger'>)[a]) || 'info'
}

function getLogToName(log: HistoryItem): string {
  if (log.to_label_name) return log.to_label_name
  const id = log.payload?.diff?.to?.label_id
  if (id != null && data.value?.current_label_name && data.value?.current_label_id === id) {
    return data.value.current_label_name
  }
  return `id=${id ?? '-'}`
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return '-'
  try {
    // 后端 datetime.utcnow() 写入的 created_at / annotated_at 不带时区后缀,
    // 实际是 UTC 时间. 直接 new Date(iso) 会被当作本地时间解析 (当前为 UTC+8),
    // 导致显示比真实时间快 8 小时. 这里显式补 'Z' 标记为 UTC, 再由 toLocaleString
    // 按浏览器本地时区 (中国为 UTC+8) 正确转换.
    const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(iso)
    const normalized = hasTz ? iso : (iso.includes('T') ? `${iso}Z` : `${iso.replace(' ', 'T')}Z`)
    return new Date(normalized).toLocaleString('zh-CN', { hour12: false })
  } catch { return iso }
}

function formatCost(ms: number): string {
  if (!ms) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}
</script>

<style scoped>
.current-block { margin-bottom: 12px; }
.current-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
}
.current-label {
  color: #606266;
  font-weight: 500;
  min-width: 110px;
}
.action-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 12px 0;
  padding: 10px 12px;
  background: #f5f7fa;
  border-radius: 4px;
}
.action-bar :deep(.el-alert) { flex: 1; }
.log-line {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
}
.log-username, .log-cost {
  color: #909399;
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
.log-flow {
  margin-top: 6px;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 4px;
}
.log-diff {
  margin-top: 4px;
  padding: 6px 10px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  color: #606266;
}
.diff-text { margin-left: 4px; }
.diff-arrow {
  margin: 0 6px;
  color: #909399;
  font-weight: bold;
}
.log-comment {
  margin-top: 4px;
  padding: 4px 8px;
  background: #fdf6ec;
  border-left: 3px solid #e6a23c;
  font-size: 12px;
  color: #606266;
}
</style>
