/**
 * useAnnotationBatch.ts
 * ===================================================
 * 标注工作台 - 批量操作 + 修正历史 composable (v3.5.0 新增, v3.6.1 精简)
 *
 * 职责:
 * 1. 封装批量操作: 批量清除标注 / 批量标记不合格 / 全选 / 清空选择
 * 2. 封装修正历史弹窗: 打开/关闭/恢复 AI 预测
 *
 * v3.5.0 P0-2 修复: refreshViewList 改用 listIds 轻量接口
 *   - 旧: imageApi.list(... page_size=100), 100 张上限, 超出 100 无法全选
 *   - 新: imageApi.listIds(... max_ids=2000), 一次拿到该 status 下完整 id 列表
 *   - viewImageList 改造为 { id } 对象数组 (只装 id, 不装其他字段, 节省内存)
 *   - 实际总数由 viewTotal 单独维护, 供顶部"共 N 张"展示
 *   - 极端数据集 (总 > 2000) 走 truncated=true 兜底, UI 可提示"超过 2000 张, 仅取前 2000"
 *
 * v3.6.1 精简:
 *   - 移除 onConfirmCorrection / onReAnnotate (分类任务顶部按钮已取消)
 *   - 类别确认由 ClassificationPanel 候选行「确认此标签」直接 emit submit
 *   - 类别修正由 ClassificationPanel 「或选择其他类别」下拉 emit submit
 *
 * 设计原则:
 * - 单一职责: 只管批量/历史, 不管切图/标注保存
 * - 状态在 composable 内自管, 父组件只通过返回的 ref/方法交互
 * - 复用现有 annotationApi (clear / batchMarkUnqualified / correctionHistory / revertToAi)
 *
 * 父组件传入:
 *   - image: 当前图 ref
 *   - datasetId: 当前 datasetId ref
 *   - statusFilter: 当前状态筛选 ref
 *   - refreshStats: 父组件的 stats 刷新函数
 *   - submit: 父组件的标注保存函数
 *   - apiStatusParam: ComputedRef (父组件 useAnnotationStatusFilter 提供的 API 参数)
 */
import { ref, computed, type Ref, type ComputedRef } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { annotationApi, imageApi } from '@/api'
import { STATUS_FILTER_LABEL, type StatusFilterValue } from './useAnnotationStatusFilter'

export interface UseAnnotationBatchOptions {
  image: Ref<any>
  datasetId: Ref<number | null>
  statusFilter: Ref<StatusFilterValue>
  apiStatusParam: ComputedRef<string | undefined>
  refreshStats: () => Promise<void>
  loadNext: () => Promise<void>
  submit: (labelId: number, labelName: string, isConfirm: boolean, comment?: string) => Promise<void>
}

export function useAnnotationBatch(options: UseAnnotationBatchOptions) {
  const {
    image, datasetId, statusFilter, apiStatusParam,
    refreshStats, loadNext, submit,
  } = options

  // ============== 选中与批量操作 state ==============
  // v3.5.0 P0-2 改造: viewImageList 仅装 { id }, 不再装完整 image 字段
  // - 旧实现: imageApi.list 返回完整 image 对象, 100 张上限
  // - 新实现: imageApi.listIds 返回 id 数组, 2000 张上限, 内存占用降一个量级
  const viewImageList = ref<Array<{ id: number }>>([])
  // 后端返回的真实总数 (可能 > viewImageList.length, 例如 total=5230 / items=2000)
  const viewTotal = ref(0)
  // 是否被 max_ids 截断 (true 时 UI 应提示用户)
  const viewTruncated = ref(false)
  const selectedIds = ref<number[]>([])
  const batchOperating = ref<'mark' | 'clear' | null>(null)

  // ============== 修正历史弹窗 state ==============
  const historyDialogVisible = ref(false)

  // ============== 派生: 状态中文标签 ==============
  const statusLabel = computed(() => STATUS_FILTER_LABEL[statusFilter.value])

  // ============== v3.5.0 P0-2: 拉取当前 statusFilter 下的全量 id 列表 (供批量操作) ==============
  // 改用 listIds 轻量接口, 单次最多 2000 张 (后端硬上限)
  // 失败时静默, viewImageList / viewTotal 保持空, 批量按钮自动 disabled
  // viewTotal 与 viewImageList.length 可能不同 (截断时), UI 可用 viewTotal 展示"共 N 张"
  async function refreshViewList(): Promise<void> {
    if (!datasetId.value) {
      viewImageList.value = []
      viewTotal.value = 0
      viewTruncated.value = false
      return
    }
    try {
      const resp: any = await imageApi.listIds(datasetId.value, {
        status: apiStatusParam.value,
        order: 'desc',
        max_ids: 2000,
      })
      const items: number[] = resp?.items || []
      viewImageList.value = items.map((id) => ({ id }))
      viewTotal.value = Number(resp?.total || 0)
      viewTruncated.value = !!resp?.truncated
      if (viewTruncated.value) {
        // 不弹强提示, 仅静默标记, 顶部 batch-bar 可按需展示
        console.warn(
          `[useAnnotationBatch] 该 status 共 ${viewTotal.value} 张, 已超过 listIds 上限 2000, 批量操作仅覆盖前 ${viewImageList.value.length} 张`,
        )
      }
    } catch (e: any) {
      // 失败时清空, 避免脏数据
      viewImageList.value = []
      viewTotal.value = 0
      viewTruncated.value = false
      console.error('[useAnnotationBatch] refreshViewList 失败:', e)
    }
  }

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
    selectedIds.value = viewImageList.value.map((it) => it.id)
  }
  function onClearSelection(): void {
    selectedIds.value = []
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
    viewTotal,
    viewTruncated,
    selectedIds,
    batchOperating,
    historyDialogVisible,
    statusLabel,
    // 批量操作
    refreshViewList,
    onBatchClear,
    onBatchMarkUnqualified,
    onSelectAll,
    onClearSelection,
    // 历史
    openHistoryDialog,
    onRevertedFromHistory,
  }
}
