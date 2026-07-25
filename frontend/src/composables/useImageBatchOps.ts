import { ref, type Ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { imageApi, annotationApi } from '@/api'

/**
 * useImageBatchOps - 图像批量/单图操作 (删除 + 清除标注)
 *
 * v3.0.0 Phase L 拆分: 从 DatasetDetail/index.vue 抽离
 *
 * 职责:
 * - 单图删除: 带确认弹窗
 * - 单图清除标注: 分类/检测/分割三种 task_type 走不同提示文案
 * - 批量删除: 跳过空选中, 弹窗确认
 * - 批量清除标注: 跨任务类型统一接口
 * - 每个操作返回 Promise<boolean> 表示是否真的执行了 (false = 用户取消)
 *
 * 父组件传入:
 *   - selectedIds: 选中图片 id 列表
 *   - removeId: 选中态管理提供的单图移除函数
 *   - reload: 列表重新加载函数
 *   - currentTaskType: 当前数据集的 task_type (用于清除标注的提示文案)
 */
export interface UseImageBatchOpsOptions {
  selectedIds: Ref<number[]>
  removeId: (id: number) => void
  reload: () => Promise<void> | void
  currentTaskType: Ref<string>
}

export function useImageBatchOps(options: UseImageBatchOpsOptions) {
  const { selectedIds, removeId, reload, currentTaskType } = options

  // loading 状态 (每个操作一个 flag, 避免相互阻塞)
  const deleting = ref(false)
  const clearing = ref(false)

  // ============== 单图操作 ==============
  /** 单图删除 */
  async function deleteOne(img: any): Promise<boolean> {
    try {
      await ElMessageBox.confirm(
        `确认删除「${img.filename}」?`,
        '提示',
        { type: 'warning' }
      )
    } catch { return false }
    try {
      deleting.value = true
      await imageApi.remove(img.id)
      ElMessage.success('已删除')
      removeId(img.id)
      await reload()
      return true
    } catch (e: any) {
      ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e?.message))
      return false
    } finally {
      deleting.value = false
    }
  }

  /** 单图清除标注 (人工 + AI + 检测 bbox + 分割 mask) */
  async function clearOneAnnotation(img: any): Promise<boolean> {
    if (!hasAnnotation(img)) {
      ElMessage.warning('该图片尚无标注, 无需去除')
      return false
    }
    const hadAi = img.status === 'ai_labeled' && img.ai_prediction
    const tt: string = img.task_type || currentTaskType.value || 'classification'
    const tipMap: Record<string, string> = {
      classification: `确认去除「${img.filename}」的人工标注? 该图片将回到待标注状态, 历史会保留在审计日志`,
      detection: `确认去除「${img.filename}」的所有检测框? 该图片将回到待标注状态, 训练/导出将不再含这些框`,
      segmentation: `确认去除「${img.filename}」的分割 mask? 该图片将回到待标注状态, 训练/导出将不再含该 mask`,
    }
    const tip = hadAi
      ? `确认去除「${img.filename}」的 AI 预标注? 该图片将回到待标注状态, 下次自动标注会重新预测`
      : (tipMap[tt] || tipMap.classification)
    try {
      await ElMessageBox.confirm(tip, '清除标注', { type: 'warning' })
    } catch { return false }
    try {
      clearing.value = true
      const r: any = await annotationApi.clear([img.id])
      const ok = r?.cleared || 0
      const skip = r?.skipped || 0
      if (ok > 0) {
        const aiN = (r?.items || []).filter((x: any) => x.ai_cleared).length
        const bboxN = r?.bbox_cleared_count || 0
        const maskN = r?.mask_cleared_count || 0
        const detailParts: string[] = []
        if (aiN > 0) detailParts.push(`含 AI 预标注 ${aiN} 张`)
        if (bboxN > 0) detailParts.push(`清理 ${bboxN} 个检测框`)
        if (maskN > 0) detailParts.push(`清理 ${maskN} 个分割 mask`)
        const detailSuffix = detailParts.length > 0 ? `, ${detailParts.join(', ')}` : ''
        ElMessage.success(
          `已清除标注 (${ok} 张${detailSuffix}${skip > 0 ? `, 跳过 ${skip} 张` : ''})`
        )
      } else {
        ElMessage.info(`无需处理 (跳过 ${skip} 张)`)
      }
      removeId(img.id)
      await reload()
      return true
    } catch (e: any) {
      ElMessage.error('清除标注失败: ' + (e?.response?.data?.detail || e?.message))
      return false
    } finally {
      clearing.value = false
    }
  }

  // ============== 批量操作 ==============
  /** 批量删除选中图片 */
  async function batchDelete(): Promise<boolean> {
    if (selectedIds.value.length === 0) {
      ElMessage.warning('请先选择图片')
      return false
    }
    try {
      await ElMessageBox.confirm(
        `确认删除选中的 ${selectedIds.value.length} 张图片? 此操作不可恢复`,
        '危险操作',
        { type: 'warning' }
      )
    } catch { return false }
    try {
      deleting.value = true
      const r: any = await imageApi.batchRemove(selectedIds.value)
      ElMessage.success(`已删除 ${r.deleted} 张${r.missing > 0 ? `, 缺失 ${r.missing} 张` : ''}`)
      selectedIds.value = []
      await reload()
      return true
    } catch (e: any) {
      ElMessage.error('删除失败: ' + (e?.response?.data?.detail || e?.message))
      return false
    } finally {
      deleting.value = false
    }
  }

  /** 批量清除标注 (人工 + AI + 检测 + 分割 一起清) */
  async function batchClearAnnotation(): Promise<boolean> {
    if (selectedIds.value.length === 0) {
      ElMessage.warning('请先选择图片')
      return false
    }
    try {
      await ElMessageBox.confirm(
        `确认对选中的 ${selectedIds.value.length} 张图片执行"清除标注"?` +
        `\n分类图会清人工/AI 类别, 检测图会清所有检测框, 分割图会清 mask 物理文件; 无标注的图片会跳过; 历史会保留在审计日志`,
        '批量清除标注',
        { type: 'warning' }
      )
    } catch { return false }
    try {
      clearing.value = true
      const r: any = await annotationApi.clear(selectedIds.value)
      const ok = r?.cleared || 0
      const skip = (r?.skipped || 0) + (r?.missing || 0)
      if (ok > 0) {
        const aiN = (r?.items || []).filter((x: any) => x.ai_cleared).length
        const bboxN = r?.bbox_cleared_count || 0
        const maskN = r?.mask_cleared_count || 0
        const detailParts: string[] = []
        if (aiN > 0) detailParts.push(`AI 预标注 ${aiN} 张`)
        if (bboxN > 0) detailParts.push(`检测框 ${bboxN} 个`)
        if (maskN > 0) detailParts.push(`分割 mask ${maskN} 个`)
        const detailSuffix = detailParts.length > 0 ? `, 清理 ${detailParts.join(', ')}` : ''
        ElMessage.success(
          `已清除标注 ${ok} 张${detailSuffix}${skip > 0 ? `, 跳过 ${skip} 张` : ''}`
        )
      } else {
        ElMessage.info('所选图片均无标注, 跳过')
      }
      selectedIds.value = []
      await reload()
      return true
    } catch (e: any) {
      ElMessage.error('批量清除标注失败: ' + (e?.response?.data?.detail || e?.message))
      return false
    } finally {
      clearing.value = false
    }
  }

  // ============== 工具: 判断是否有标注 (与 page 行为一致) ==============
  function hasAnnotation(img: any): boolean {
    if (!img) return false
    if (img.final_label_id) return true
    if (['human_confirmed', 'human_corrected', 'trained', 'ai_labeled'].includes(img.status)) {
      return true
    }
    if ((img.bbox_count || 0) > 0) return true
    if (img.has_mask) return true
    return false
  }

  return {
    deleting, clearing,
    deleteOne, clearOneAnnotation,
    batchDelete, batchClearAnnotation,
    hasAnnotation,
  }
}
