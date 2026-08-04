/**
 * useAnnotationAICorrection.ts
 * ===================================================
 * 标注工作台 - AI 已标图片修正事件处理 composable (v3.5.0 新增, 从 index.vue 抽出)
 *
 * 职责 (统一管理 DetectionPanel / SegmentationPanel 的"AI 修正"双路径事件):
 * 1. onConfirmDetectionCorrection:
 *    - 接受当前 AI bbox 预测 → 走 saveDetectionBBoxes → 状态 -> human_confirmed
 *    - v3.6.0: categoryId/labelName 透传到 audit comment (主类别记入日志)
 * 2. onReAnnotateDetection:
 *    - 弹窗确认 → 调 detectionApi.clearBBoxes + 清空 bboxList → 用户重画
 * 3. onConfirmSegmentationCorrection:
 *    - 走子组件 segAnnotRef.save() → 状态 -> human_confirmed
 *    - v3.6.0: categoryId/labelName 透传到 audit comment (mask 类别)
 * 4. onReAnnotateSegmentation:
 *    - 弹窗确认 → 列后端 mask 逐个 removeMask + revokeMaskUrl → 用户重画
 *
 * 设计原则:
 * - 仅处理检测/分割任务的「确认修正/重新标注」双路径
 *   · 分类任务 (ClassificationPanel) 已在 v3.6.1 移除这两个按钮
 *   · 类别确认: 候选行「确认此标签」直接 emit submit
 *   · 类别修正: 「或选择其他类别」下拉 emit submit
 * - 单向数据流: 父组件只传 ref 和 save 函数, composable 不修改 image.value
 * - 自动跳下一张: 栈顶时自动 loadNext, 中间时不跳 (与其它保存路径保持一致)
 */
import { type Ref, type ComputedRef } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { detectionApi, segmentationApi } from '@/api'

export interface UseAnnotationAICorrectionOptions {
  image: Ref<any>
  detAnnotRef: Ref<any>
  segAnnotRef: Ref<any>
  annotatorSaving: Ref<boolean>
  bboxList: Ref<any[]>
  historyCursor: Ref<number>
  historyIds: Ref<number[]>
  saveDetectionBBoxes: (bboxes: any[]) => Promise<void>
  revokeMaskUrl: () => void
  refreshStats: () => Promise<void>
  loadNext: () => Promise<void>
}

export function useAnnotationAICorrection(options: UseAnnotationAICorrectionOptions) {
  const {
    image, detAnnotRef, segAnnotRef, annotatorSaving,
    bboxList, historyCursor, historyIds,
    saveDetectionBBoxes, revokeMaskUrl, refreshStats, loadNext,
  } = options

  /**
   * v3.6.0: 检测: 确认修正 — 把当前 bboxList 落库, 状态 -> human_confirmed
   * - categoryId / labelName 来自 AICorrectionCategoryDialog 弹窗 (用户选的主类别)
   * - 仅作 UI 反馈: ElMessage 展示「主类别: X」, 让用户明确知道选了什么
   * - 实际 bbox 仍按 bboxList 保存 (主类别不影响 bbox 数据, 仅作审计/确认标识)
   * - 后端 save_bbox 接口自身的 audit log (action='save') 已记录此次修正
   */
  const onConfirmDetectionCorrection = async (categoryId?: number, labelName?: string) => {
    if (annotatorSaving.value) return
    try {
      annotatorSaving.value = true
      const cur = detAnnotRef.value?.modelValue || bboxList.value
      await saveDetectionBBoxes(cur)
      // v3.6.0: 弹窗用户已选主类别, 给个明确反馈
      if (labelName) {
        ElMessage.success(`已确认修正 (主类别: ${labelName})`)
      }
      await refreshStats()
      if (historyCursor.value >= historyIds.value.length - 1) {
        await loadNext()
      }
    } catch (e: any) {
      ElMessage.error('确认修正失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      annotatorSaving.value = false
    }
  }

  /** 检测: 重新标注 — 清空当前 AI bbox, 让用户在画布上重画 */
  const onReAnnotateDetection = async () => {
    if (!image.value?.id) return
    try {
      await ElMessageBox.confirm(
        '清空当前 AI 预标注的 bbox, 让您重新画。历史会保留在审计日志。',
        '重新标注',
        { type: 'warning' }
      )
    } catch { return }
    try {
      annotatorSaving.value = true
      await detectionApi.clearBBoxes(image.value.id)
      bboxList.value = []
      // 不 loadNext - 让用户直接在画布上重画, 画完点保存
      ElMessage.success('已清空, 请重新标注')
    } catch (e: any) {
      ElMessage.error('清空失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      annotatorSaving.value = false
    }
  }

  /**
   * v3.6.0: 分割: 确认修正 — 走子组件 save: 把当前画布上的 mask 保存为 human 源, 状态 -> human_confirmed
   * - categoryId / labelName 来自 AICorrectionCategoryDialog 弹窗 (用户选的 mask 类别)
   * - 分割任务 mask 是单类, labelName 表示用户最终选定的 mask 类别
   * - 后端 save_mask 接口自身的 audit log (action='save') 已记录此次修正
   */
  const onConfirmSegmentationCorrection = async (categoryId?: number, labelName?: string) => {
    if (annotatorSaving.value) return
    try {
      annotatorSaving.value = true
      segAnnotRef.value?.save?.()
      // v3.6.0: 弹窗用户已选 mask 类别, 给个明确反馈
      if (labelName) {
        ElMessage.success(`已确认修正 (mask 类别: ${labelName})`)
      }
      await refreshStats()
      if (historyCursor.value >= historyIds.value.length - 1) {
        await loadNext()
      }
    } catch (e: any) {
      ElMessage.error('确认修正失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      annotatorSaving.value = false
    }
  }

  /** 分割: 重新标注 — 列后端 mask 逐个 removeMask + revokeMaskUrl */
  const onReAnnotateSegmentation = async () => {
    if (!image.value?.id) return
    try {
      await ElMessageBox.confirm(
        '清空当前 AI 预标注的 mask, 让您重新画。历史会保留在审计日志。',
        '重新标注',
        { type: 'warning' }
      )
    } catch { return }
    try {
      annotatorSaving.value = true
      // 后端 mask 元数据存在时, 调 removeMask; 否则只是前端 revoke blob
      try {
        const list: any = await segmentationApi.listMasks([image.value.id])
        const items: any[] = list?.items || list || []
        for (const m of items) {
          if (m?.id) await segmentationApi.removeMask(m.id)
        }
      } catch {}
      revokeMaskUrl()
      ElMessage.success('已清空, 请重新标注')
    } catch (e: any) {
      ElMessage.error('清空失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      annotatorSaving.value = false
    }
  }

  return {
    onConfirmDetectionCorrection,
    onReAnnotateDetection,
    onConfirmSegmentationCorrection,
    onReAnnotateSegmentation,
  }
}
