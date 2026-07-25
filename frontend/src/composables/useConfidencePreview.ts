import { computed, ref, type Ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { imageApi } from '@/api'

/**
 * useConfidencePreview - 置信度测评 (非破坏性 dry-run)
 *
 * v3.0.0 Phase L 拆分: 从 DatasetDetail/index.vue 抽离
 *
 * 职责:
 * - 维护测评状态: previewing / previewDialogVisible / previewResult
 * - 提供 onPreviewConfidence 方法, 调 /api/images/preview-confidence
 * - 提供 previewTooltipText 给「测评」按钮 tooltip 用 (按 task_type 动态切换)
 * - 硬性约束: 测评必须用本数据集训练出的 fine-tune 模型, 防止 timm 兜底出与项目业务无关结果
 */
export interface UseConfidencePreviewOptions {
  /** 当前数据集 id */
  datasetId: Ref<number | null>
  /** 当前显示的图像列表 (用于取 ids 给后端) */
  images: Ref<any[]>
  /** 当前数据集任务类型 (用于 tooltip 文案分派) */
  currentTaskType: Ref<'classification' | 'detection' | 'segmentation'>
  /** 是否存在 fine-tune 模型 */
  hasFinetuneModel: Ref<boolean>
  /** 当前选中的 fine-tune 模型 id */
  selectedFinetuneId: Ref<number | null>
  /** 置信度阈值 (slider 值) */
  threshold: Ref<number>
}

export function useConfidencePreview(options: UseConfidencePreviewOptions) {
  const {
    datasetId, images,
    currentTaskType, hasFinetuneModel,
    selectedFinetuneId, threshold,
  } = options
  const router = useRouter()

  // ============== 状态 ==============
  const previewing = ref(false)
  const previewDialogVisible = ref(false)
  const previewResult = ref<any | null>(null)

  // ============== Tooltip 文案 (按任务类型分派) ==============
  /**
   * v2.5.46: 测评按钮的 tooltip 文案 — 按任务类型动态切换
   * - 分类: top-1 标签 + 置信度 + top-3 候选
   * - 检测: bbox 计数 + 最高置信度 + bbox 类别摘要
   * - 分割: 最大 softmax + 像素级 mask
   * 无 fine-tune 模型时统一走"无模型引导"提示
   */
  const previewTooltipText = computed(() => {
    if (!hasFinetuneModel.value) {
      return '该数据集暂无训练模型, 请先到「训练任务」页选定该数据集启动训练'
    }
    const tt = currentTaskType.value
    if (tt === 'detection') {
      return 'dry-run 试跑当前页图片, 不写库, 弹窗显示 bbox 计数 + 最高置信度 + 类目摘要, 帮你在执行批量预标注前评估阈值是否合适'
    }
    if (tt === 'segmentation') {
      return 'dry-run 试跑当前页图片, 不写库, 弹窗显示每张图的最大 softmax 与是否会被落标, 帮你在执行批量预标注前评估阈值是否合适'
    }
    return 'dry-run 试跑当前页图片, 不写库, 弹窗显示 3 类: 会标/待标/无交集, 帮你在执行批量预标注前评估阈值是否合适'
  })

  // ============== 启动测评 ==============
  /**
   * 调 /api/images/preview-confidence
   * 硬性约束: 测评必须用本数据集训练出的 fine-tune 模型
   * 防止用户用 timm ImageNet 基础模型测评出与项目业务无关的结果
   */
  async function onPreviewConfidence(): Promise<boolean> {
    if (!datasetId.value) return false
    if (images.value.length === 0) {
      ElMessage.warning('当前页没有图片, 请调整过滤条件或翻页')
      return false
    }
    if (!hasFinetuneModel.value || !selectedFinetuneId.value) {
      try {
        await ElMessageBox.confirm(
          '该数据集暂无训练模型, 无法进行置信度测评。\n请先到「训练任务」页选定该数据集启动训练, 完成后即可用本数据集专属模型测评。',
          '缺少数据集模型',
          {
            type: 'warning',
            confirmButtonText: '前往训练任务',
            cancelButtonText: '稍后再说',
          }
        )
        router.push('/training')
      } catch { /* 用户取消 */ }
      return false
    }
    previewing.value = true
    try {
      const ids = images.value.map((img: any) => img.id)
      const res: any = await imageApi.previewConfidence(datasetId.value, ids, {
        model_id: selectedFinetuneId.value,
        confidence_threshold: threshold.value,
        use_finetune: true,
      })
      previewResult.value = res
      previewDialogVisible.value = true
      return true
    } catch (e: any) {
      ElMessage.error('测评失败: ' + (e?.response?.data?.detail || e?.message))
      return false
    } finally {
      previewing.value = false
    }
  }

  // ============== 关闭弹窗 ==============
  function closePreviewDialog() {
    previewDialogVisible.value = false
  }

  return {
    previewing, previewDialogVisible, previewResult,
    previewTooltipText,
    onPreviewConfidence, closePreviewDialog,
  }
}
