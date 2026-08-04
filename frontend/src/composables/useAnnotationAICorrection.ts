/**
 * useAnnotationAICorrection.ts
 * ===================================================
 * 标注工作台 - AI 已标图片修正事件处理 composable (v3.5.0 新增, 从 index.vue 抽出)
 *
 * 职责 (统一管理 DetectionPanel / SegmentationPanel 的"AI 修正"双路径事件):
 * 1. onConfirmDetectionCorrection:
 *    - 接受当前 AI bbox 预测 → 走 saveDetectionBBoxes → 状态 -> human_confirmed
 * 2. onReAnnotateDetection:
 *    - 弹窗确认 → 调 detectionApi.clearBBoxes + 清空 bboxList → 用户重画
 * 3. onConfirmSegmentationCorrection:
 *    - 走子组件 segAnnotRef.save() → 状态 -> human_confirmed
 * 4. onReAnnotateSegmentation:
 *    - 弹窗确认 → 列后端 mask 逐个 removeMask + revokeMaskUrl → 用户重画
 *
 * 设计原则:
 * - 与 useAnnotationBatch 内 onConfirmCorrection / onReAnnotate 区别:
 *   · 本 composable 处理**单图右侧面板**的"确认修正/重新标注"按钮 (Detection/Segmentation)
 *   · useAnnotationBatch 处理**批量 + 分类图**的修正事件 (来自 ClassificationPanel + BatchBar)
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

  /** 检测: 确认修正 — 把当前 bboxList 落库, 状态 -> human_confirmed */
  const onConfirmDetectionCorrection = async () => {
    if (annotatorSaving.value) return
    try {
      annotatorSaving.value = true
      const cur = detAnnotRef.value?.modelValue || bboxList.value
      await saveDetectionBBoxes(cur)
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

  /** 分割: 确认修正 — 走子组件 save: 把当前画布上的 mask 保存为 human 源, 状态 -> human_confirmed */
  const onConfirmSegmentationCorrection = async () => {
    if (annotatorSaving.value) return
    try {
      annotatorSaving.value = true
      segAnnotRef.value?.save?.()
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
