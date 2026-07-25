import { ref, computed, watch, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import { modelApi, autoAnnotateApi } from '@/api'

/**
 * useAiLabeling - AI 预标注 (模型选择 + 启动 + 测评引导)
 *
 * v3.0.0 Phase L 拆分: 从 DatasetDetail/index.vue 抽离
 *
 * 职责:
 * - 加载本数据集已激活的 fine-tune 模型列表 (与 Annotate 工作台同源)
 * - 默认选择: 已选仍存在 > 第一个; 否则清空
 * - 启动 AI 预标注 (调 /api/images/auto-label), 后端会自动处理 timm 冷启动兜底
 * - 提供 displayModel / selectedFinetuneModel / hasFinetuneModel 三个计算属性给 AI 配置提示行用
 */
export interface UseAiLabelingOptions {
  /** 当前数据集 id (ref 形式, 切换数据集时自动重载) */
  datasetId: Ref<number | null>
  /** 加载完成后回调 (供父组件在 load 末尾串行调用) */
  onAfterLoad?: () => void | Promise<void>
}

export function useAiLabeling(options: UseAiLabelingOptions) {
  const { datasetId, onAfterLoad } = options

  // ============== 状态 ==============
  const finetuneModels = ref<any[]>([])
  const selectedFinetuneId = ref<number | null>(null)
  const activeModel = ref<any>(null)        // 当前数据集激活的 fine-tune 模型 (与 Annotate 命名一致)
  const threshold = ref(0.6)
  const autoLabeling = ref(false)

  // ============== 计算属性 ==============
  /**
   * 当前选中的 fine-tune 模型对象 (用于 el-option label 生成 / 提示文案)
   */
  const selectedFinetuneModel = computed(
    () => finetuneModels.value.find((m: any) => m.id === selectedFinetuneId.value) || null
  )

  /**
   * 是否存在本数据集的 fine-tune 模型
   * - false 时: 测评按钮 disabled + 友好提示, 引导去训练
   * - true 时:  正常测评
   */
  const hasFinetuneModel = computed(() => finetuneModels.value.length > 0)

  /**
   * 当前生效模型的显示文案 (用于 AI 配置提示行)
   * - 选中 fine-tune: 显示「<name> (基础模型 resnet50, 准确率 92.4%)」
   * - 未选 (无 fine-tune): 显示「暂未训练, AI 预标注将自动走 timm 冷启动」
   */
  const displayModel = computed(() => {
    const m = selectedFinetuneModel.value
    if (m) {
      const acc = ((m.accuracy || 0) * 100).toFixed(1)
      return `${m.name} (基础 ${m.base_model}, 准确率 ${acc}%)`
    }
    return '暂未训练 fine-tune 模型, 将自动回退 timm 预训练 (冷启动)'
  })

  // ============== 加载 fine-tune 模型列表 ==============
  /**
   * 与 Annotate 工作台 refreshFinetuneModels 行为完全一致:
   * - 仅显示本数据集**已激活**的 fine-tune 模型
   * - 默认选择: 已选仍存在 > 第一个
   * - 无激活时清空下拉
   */
  async function loadFinetuneModels() {
    const did = datasetId.value
    if (!did) {
      finetuneModels.value = []
      selectedFinetuneId.value = null
      return
    }
    try {
      let items: any[] = []
      try {
        const r: any = await modelApi.list({ dataset_id: did, active: true })
        items = r?.items || r || []
      } catch {
        items = []
      }
      if (items.length === 0) {
        const r: any = await modelApi.getActive(did)
        items = r?.items || (r?.model ? [r.model] : [])
      }
      finetuneModels.value = items
      if (items.length > 0) {
        const prev = selectedFinetuneId.value
        const stillExists = items.find((m: any) => m.id === prev)
        selectedFinetuneId.value = stillExists ? prev : items[0].id
      } else {
        selectedFinetuneId.value = null
      }
    } catch {
      finetuneModels.value = []
      selectedFinetuneId.value = null
    }
  }

  /**
   * 拉取当前数据集的激活模型 (与 Annotate 同源)
   * 注: 通常在父组件 load() 里走 Promise.all, 这里只暴露 ref
   */
  async function fetchActiveModel() {
    if (!datasetId.value) return
    try {
      const r: any = await modelApi.getActive(datasetId.value)
      activeModel.value = r?.model || r?.items?.[0] || null
    } catch {
      activeModel.value = null
    }
  }

  // ============== 启动 AI 预标注 ==============
  /**
   * 调 /api/images/auto-label 接口:
   *   - use_finetune=true 时: 后端优先用 model_id 指定的 fine-tune; 缺省 = 激活
   *   - 无任何 fine-tune 时: 后端自动 fallback 到 timm pretraining (冷启动)
   *   - 不再调用旧的 /auto-annotate/run (已统一到 auto-label)
   */
  async function onAutoAnnotate(): Promise<boolean> {
    const did = datasetId.value
    if (!did) return false
    // 友好提示: 项目无 fine-tune 模型时, 后端会自动走 timm 冷启动
    if (finetuneModels.value.length === 0) {
      ElMessage.info('该项目暂无 fine-tune 模型, AI 预标注将自动回退到 timm ImageNet 预训练 (冷启动兜底)')
    }
    autoLabeling.value = true
    try {
      const res: any = await autoAnnotateApi.autoLabel(did, {
        model_id: selectedFinetuneId.value ?? undefined,
        confidence_threshold: threshold.value,
        use_finetune: true,
      })
      const usedName = res.model_name
        || selectedFinetuneModel.value?.name
        || 'timm 预训练 (冷启动)'
      const noMatch = res.no_match || 0
      const noMatchTip = noMatch > 0
        ? `, 无匹配 ${noMatch} 张 (输出与项目类目无交集, 已保持待标注)`
        : ''
      ElMessage.success(
        `[${usedName}] 共 ${res.total} 张, 命中 ${res.auto_labeled} 张, ` +
        `需人工 ${res.need_human} 张${noMatchTip}, 平均置信度 ${(res.avg_confidence * 100).toFixed(1)}%`
      )
      return true
    } catch (e: any) {
      ElMessage.error('AI 预标注失败: ' + (e?.response?.data?.detail || e?.message))
      return false
    } finally {
      autoLabeling.value = false
    }
  }

  // ============== 切换数据集时重置选中 ==============
  watch(datasetId, () => {
    selectedFinetuneId.value = null
  })

  return {
    // 状态
    finetuneModels, selectedFinetuneId, activeModel, threshold, autoLabeling,
    // 计算属性
    selectedFinetuneModel, hasFinetuneModel, displayModel,
    // 操作
    loadFinetuneModels, fetchActiveModel, onAutoAnnotate,
  }
}
