/**
 * useAnnotationBatch.ts
 * ===================================================
 * 标注工作台 - 批量操作 + 修正历史 + AI 修正事件 composable (v3.5.0 新增)
 *
 * 职责:
 * 1. 封装批量操作: 批量清除标注 / 批量标记不合格 / 全选 / 清空选择
 * 2. 封装修正历史弹窗: 打开/关闭/恢复 AI 预测
 * 3. 封装 AI 已标图片「确认修正」/「重新标注」事件
 *
 * 设计原则:
 * - 单一职责: 只管批量/历史/AI 修正事件, 不管切图/标注保存
 * - 状态在 composable 内自管, 父组件只通过返回的 ref/方法交互
 * - 复用现有 annotationApi (clear / batchMarkUnqualified / correctionHistory / revertToAi)
 *
 * 父组件传入:
 *   - image: 当前图 ref
 *   - datasetId: 当前 datasetId ref
 *   - statusFilter: 当前状态筛选 ref
 *   - categories: 当前类目 ref
 *   - refreshStats: 父组件的 stats 刷新函数
 *   - refreshViewList: 父组件的列表刷新函数
 *   - loadNext: 父组件的下一张图函数
 *   - submit: 父组件的标注保存函数 (供 onConfirmCorrection 复用)
 *   - imageApi: api 实例 (供重新拉详情)
 */
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { annotationApi, imageApi } from '@/api'
import type { Ref } from 'vue'
import { STATUS_FILTER_LABEL, type StatusFilterValue } from './useAnnotationStatusFilter'

export interface UseAnnotationBatchOptions {
  image: Ref<any>
  datasetId: Ref<number | null>
  statusFilter: Ref<StatusFilterValue>
  refreshStats: () => Promise<void>
  refreshViewList: () => Promise<void>
  loadNext: () => Promise<void>
  submit: (labelId: number, labelName: string, isConfirm: boolean, comment?: string) => Promise<void>
}

export function useAnnotationBatch(options: UseAnnotationBatchOptions) {
  const {
    image, datasetId, statusFilter,
    refreshStats, refreshViewList, loadNext, submit,
  } = options

  // ============== 选中与批量操作 state ==============
  const viewImageList = ref<any[]>([])
  const selectedIds = ref<number[]>([])
  const batchOperating = ref<'mark' | 'clear' | null>(null)

  // ============== 修正历史弹窗 state ==============
  const historyDialogVisible = ref(false)

  // ============== 派生: 状态中文标签 ==============
  const statusLabel = computed(() => STATUS_FILTER_LABEL[statusFilter.value])

  // ============== 批量操作 ==============
  async function onBatchClear(): Promise<void> {
    if (selectedIds.value.length === 0) {
      ElMessage.warning('请先选择图片')
      return
    }
    try {
      await ElMessageBox.confirm(
        `确认对选中的 ${selectedIds.value.length} 张图片执行"批量清除标注"?` +
        `\n分类图会清人工/AI 类别, 检测图会清所有检测框, 分割图会清 mask 物理文件; 历史会保留在审计日志`,
        '批量清除标注',
        { type: 'warning' }
      )
    } catch { return }
    try {
      batchOperating.value = 'clear'
      const r: any = await annotationApi.clear(selectedIds.value)
      const ok = r?.cleared || 0
      const skip = (r?.skipped || 0) + (r?.missing || 0)
      ElMessage.success(
        `已清除 ${ok} 张${skip > 0 ? `, 跳过 ${skip} 张` : ''}`
      )
      selectedIds.value = []
      await refreshStats()
      await refreshViewList()
      loadNext()
    } catch (e: any) {
      ElMessage.error('批量清除失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      batchOperating.value = null
    }
  }

  async function onBatchMarkUnqualified(reason: string): Promise<void> {
    if (selectedIds.value.length === 0) return
    try {
      batchOperating.value = 'mark'
      const r: any = await annotationApi.batchMarkUnqualified({
        image_ids: selectedIds.value,
        reason,
      })
      const ok = r?.marked || r?.updated || selectedIds.value.length
      ElMessage.success(`已标记 ${ok} 张为不合格`)
      selectedIds.value = []
      await refreshStats()
      await refreshViewList()
    } catch (e: any) {
      ElMessage.error('批量标记失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      batchOperating.value = null
    }
  }

  function onSelectAll(): void {
    selectedIds.value = viewImageList.value.map((it: any) => it.id)
  }
  function onClearSelection(): void {
    selectedIds.value = []
  }

  // ============== AI 已标图片「确认修正」/「重新标注」 ==============
  function onConfirmCorrection(labelId: number, labelName: string): void {
    // 走与 submit 一样的 save 路径, 仅 comment 不同 (用于审计区分)
    submit(labelId, labelName, true, '[AI修正-确认] 已审阅 AI 预测, 确认采纳 (top1)')
  }

  function onReAnnotate(): void {
    ElMessage.info('请从下方「或选择其他类别」下拉中选择正确类别, 系统会自动记录与 AI 预测的差异')
  }

  // ============== 修正历史弹窗 ==============
  function openHistoryDialog(): void {
    if (!image.value?.id) {
      ElMessage.warning('请先选择一张图片')
      return
    }
    historyDialogVisible.value = true
  }

  async function onRevertedFromHistory(): Promise<void> {
    await refreshStats()
    if (image.value?.id) {
      try {
        const detail: any = await imageApi.detail(image.value.id)
        // 拉详情后, 让父组件用 fillImage 重新填充 (这里用 image.value 直接更新, 简化)
        image.value = detail
      } catch {}
    }
  }

  return {
    // state
    viewImageList,
    selectedIds,
    batchOperating,
    historyDialogVisible,
    statusLabel,
    // 批量操作
    onBatchClear,
    onBatchMarkUnqualified,
    onSelectAll,
    onClearSelection,
    // AI 修正事件
    onConfirmCorrection,
    onReAnnotate,
    // 历史
    openHistoryDialog,
    onRevertedFromHistory,
  }
}
