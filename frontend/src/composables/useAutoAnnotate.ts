/**
 * useAutoAnnotate.ts
 * ===================================================
 * AI 预标注 composable (v2.5.7 拆分自 Annotate.vue)
 *
 * 职责:
 * - 分类任务的 AI 预标注 (走 fine-tune / ImageNet 预训练)
 * - 检测任务的 AI 预标注 (走 fine-tune / 预训练 yolov8n/s/m/l/x)
 * - 分割任务的 AI 预标注 (仅走 fine-tune; 分割暂无预训练模型自动标注接口)
 * - 严格模式: 默认走 fine-tune, 走基础模型前弹窗警告
 *
 * 依赖:
 * - 入参: datasetId, threshold, iouThreshold, useFinetune, selectedModelId,
 *         modelName, detectionModelName, finetuneModels, activeModel
 * - 副作用: refreshStats (父组件定义), loadNext (父组件定义)
 * - 输出: autoLabeling (loading 状态)
 */
import { ref, type Ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { autoAnnotateApi, detectionApi, modelApi, segmentationApi } from '@/api'

export function useAutoAnnotate(options: {
  datasetId: Ref<number | null>
  threshold: Ref<number>
  iouThreshold: Ref<number>
  useFinetune: Ref<boolean>
  selectedModelId: Ref<number | null>
  modelName: Ref<string>
  detectionModelName: Ref<string>
  finetuneModels: Ref<any[]>
  activeModel: Ref<any>
  refreshStats: () => Promise<void> | void
  loadNext: () => Promise<void> | void
}) {
  const {
    datasetId, threshold, iouThreshold, useFinetune,
    selectedModelId, modelName, detectionModelName,
    finetuneModels, activeModel, refreshStats, loadNext,
  } = options

  /** AI 预标注中 (loading 态) */
  const autoLabeling = ref(false)

  /**
   * 启动按钮 dispatcher: 按当前 dataset task_type 分派
   * - 共享 useFinetune state, 由父组件按 dataset 自动隔离
   * - 分割任务: 关闭 useFinetune 时弹 warning 阻止 (后端无预训练分割接口)
   */
  const onStartAutoLabelClick = async (taskType: string) => {
    if (!datasetId.value) {
      ElMessage.warning('请先选择数据集')
      return
    }
    if (taskType === 'classification') {
      await runAutoAnnotate()
    } else if (taskType === 'detection') {
      await runDetectionAutoAnnotate()
    } else if (taskType === 'segmentation') {
      await runSegmentationAutoAnnotate()
    } else {
      ElMessage.warning('未知任务类型: ' + taskType)
    }
  }

  /**
   * 分类任务 AI 预标注
   */
  const runAutoAnnotate = async () => {
    if (!datasetId.value) { ElMessage.warning('请先选择数据集'); return }
    // 严格模式: 走基础模型前弹窗告知
    if (!useFinetune.value) {
      try {
        await ElMessageBox.confirm(
          [
            '当前选择「ImageNet 基础模型」。该模型输出 (class_532 等) 不在项目类目内,',
            '前端会统一归类为「未知」, 强制人工从下拉框选类目。',
            '',
            '建议: 训练项目 fine-tune 模型后再做预标注。是否继续使用基础模型?',
          ].join('\n'),
          '基础模型预标注确认',
          {
            confirmButtonText: '继续用基础模型',
            cancelButtonText: '切到 Fine-tune',
            type: 'warning',
          }
        )
      } catch {
        useFinetune.value = true
        ElMessage.info('已切换到项目训练模型')
        return
      }
    } else if (finetuneModels.value.length === 0) {
      ElMessage.warning(
        '当前项目还没有训练好的 fine-tune 模型! 请先到「训练任务」页训练一个模型并激活, 再回这里做预标注。'
      )
      return
    }
    autoLabeling.value = true
    try {
      if (useFinetune.value) {
        const resp: any = await autoAnnotateApi.autoLabel(datasetId.value, {
          confidence_threshold: threshold.value,
          use_finetune: true,
          model_id: selectedModelId.value || undefined,
        })
        // 刷新激活模型 (后端可能回退)
        try {
          const r: any = await modelApi.getActive(datasetId.value)
          const refreshed = r?.items?.[0] || r?.model || null
          if (refreshed) activeModel.value = refreshed
        } catch {}
        if (resp.used_finetune) {
          ElMessage.success(
            `[Fine-tune ${resp.model_name}] 共 ${resp.total} 张, 命中 ${resp.auto_labeled} 张, 需人工 ${resp.need_human} 张, 平均置信度 ${(resp.avg_confidence * 100).toFixed(1)}%`
          )
        } else if (resp.message) {
          const selected = finetuneModels.value.find((m) => m.id === selectedModelId.value)
          const labelName = selected?.name || activeModel.value?.name || resp.model_name || 'Fine-tune'
          ElMessage.info(`[${labelName}] ${resp.message}`)
        } else {
          ElMessage.warning(
            `[回退 → 基础模型 ${resp.model_name || 'ImageNet'}] ${resp.warning || '当前没有激活的 fine-tune 模型, 已回退到 ImageNet 预训练'}`
          )
        }
      } else {
        const resp: any = await autoAnnotateApi.run({
          dataset_id: datasetId.value,
          model_name: modelName.value,
          confidence_threshold: threshold.value,
        })
        ElMessage.warning(
          `[基础模型 ${modelName.value}] 输出已被前端归一为「未知」, 请人工标注. ` +
          `共 ${resp.total} 张, 需人工 ${resp.need_human} 张`
        )
      }
      await refreshStats()
      // 部分图被标为 ai_labeled 后, 让用户重新点「下一张」看
      await loadNext()
    } catch (e: any) {
      ElMessage.error('AI 预标注失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      autoLabeling.value = false
    }
  }

  /**
   * 检测任务 AI 预标注 (走 fine-tune 或预训练 yolov8*)
   */
  const runDetectionAutoAnnotate = async () => {
    if (!datasetId.value) { ElMessage.warning('请先选择数据集'); return }
    if (!useFinetune.value) {
      try {
        await ElMessageBox.confirm(
          [
            `当前使用「预训练 ${detectionModelName.value}」(COCO 80 类).`,
            '仅当数据集类目名与 COCO 类目重合时, 才会写入 BBoxAnnotation.',
            '建议: 训练项目 fine-tune 模型后再做预标注, 准确率更高.',
            '',
            '是否继续?',
          ].join('\n'),
          '预训练模型预标注确认',
          { confirmButtonText: '继续', cancelButtonText: '取消', type: 'warning' }
        )
      } catch {
        return
      }
    } else if (finetuneModels.value.length === 0) {
      ElMessage.warning('当前项目还没有训练好的 fine-tune 模型! 请先训练一个再回这里做预标注.')
      return
    }
    autoLabeling.value = true
    try {
      let resp: any
      if (useFinetune.value) {
        resp = await detectionApi.startAutoAnnotate({
          dataset_id: datasetId.value,
          model_version_id: selectedModelId.value ?? 0,
          conf_threshold: threshold.value,
          iou_threshold: iouThreshold.value,
        })
      } else {
        resp = await detectionApi.startAutoAnnotatePretrained({
          dataset_id: datasetId.value,
          model_name: detectionModelName.value,
          conf_threshold: threshold.value,
          iou_threshold: iouThreshold.value,
        })
      }
      const matched = resp.matched_coco_classes || []
      ElMessage.info(
        `[预训练 ${detectionModelName.value}] 任务已入队, 等待 Celery worker 启动...` +
        (matched.length
          ? ` 匹配 COCO 类: ${matched.join(', ')}`
          : ' 未匹配任何 COCO 类, 将无结果')
      )
    } catch (e: any) {
      ElMessage.error('AI 预标注失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      autoLabeling.value = false
    }
  }

  /**
   * 分割任务 AI 预标注 (仅走 fine-tune; 分割暂无预训练模型自动标注接口)
   * - 与分类/检测对齐: useFinetune 关闭时弹 warning 阻止, 引导用户切回项目模型
   * - 走后端 POST /api/segmentation/auto-annotate?model_version_id=N&dataset_id=D
   *   (后端 worker: auto_annotate_segmentation_task → predict_to_mask_image → 写 SegmentationMask source=ai)
   */
  const runSegmentationAutoAnnotate = async () => {
    if (!datasetId.value) { ElMessage.warning('请先选择数据集'); return }
    if (!useFinetune.value) {
      ElMessage.warning(
        '分割任务暂未提供预训练模型自动标注接口. 请保持「项目模型」开关开启, 训练 fine-tune 模型后再做预标注.'
      )
      return
    }
    if (finetuneModels.value.length === 0) {
      ElMessage.warning(
        '当前项目还没有训练好的 fine-tune 模型! 请先到「训练任务」页训练一个分割模型并激活, 再回这里做预标注.'
      )
      return
    }
    if (!selectedModelId.value) {
      ElMessage.warning('请先在「项目模型」下拉中选择一个 fine-tune 模型.')
      return
    }
    autoLabeling.value = true
    try {
      const resp: any = await segmentationApi.startAutoAnnotate({
        dataset_id: datasetId.value,
        model_version_id: selectedModelId.value,
      })
      // 后端返回 { task_id, state, message }
      const taskMsg = resp?.task_id
        ? `任务 ID: ${resp.task_id}`
        : (resp?.message || '已入队')
      ElMessage.success(
        `[分割 Fine-tune] 任务已入队, 等待 Celery worker 启动... ${taskMsg}`
      )
      // 任务异步执行, 前端不阻塞等待完成; 仅刷新 stats 让用户看到 mask 数量变化
      await refreshStats()
    } catch (e: any) {
      ElMessage.error('AI 预标注失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      autoLabeling.value = false
    }
  }

  return {
    autoLabeling,
    onStartAutoLabelClick,
    runAutoAnnotate,
    runDetectionAutoAnnotate,
    runSegmentationAutoAnnotate,
  }
}
