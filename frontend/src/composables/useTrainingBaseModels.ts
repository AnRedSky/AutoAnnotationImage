/**
 * useTrainingBaseModels - 基础模型动态加载 composable
 *
 * v3.0.0 Phase J 拆分: 从 Training/index.vue 抽离
 * - 异步加载 /api/auto-annotate/models
 * - 按 task_type 分组 (供训练/标注下拉用)
 * - 失败回退到 DEFAULT_BASE_MODEL (避免 UI 空白)
 *
 * 数据形状: 复用 BaseModelSelect 导出的 BaseModelOption (单一 schema)
 */
import { ref, computed } from 'vue'
import { autoAnnotateApi } from '@/api'
import { DEFAULT_BASE_MODEL, getDefaultBaseModel } from '@/utils/taskType'
import type { BaseModelOption } from '@/views/Training/components/BaseModelSelect.vue'

export function useTrainingBaseModels() {
  const all = ref<BaseModelOption[]>([])
  const loading = ref(false)

  const byTask = computed<Record<string, BaseModelOption[]>>(() => {
    const grouped: Record<string, BaseModelOption[]> = {
      classification: [],
      detection: [],
      segmentation: [],
    }
    for (const m of all.value) {
      if (m.task_type && grouped[m.task_type]) grouped[m.task_type].push(m)
    }
    return grouped
  })

  /** 任务类型候选 (空数组兜底, 供模板 disable 状态判定) */
  const getOptionsFor = (taskType: string): BaseModelOption[] =>
    byTask.value[taskType] || []

  /** 任务类型默认基础模型 (取第一项 / fallback DEFAULT_BASE_MODEL) */
  const getDefaultFor = (taskType: string): string => {
    const opts = byTask.value[taskType] || []
    return opts[0]?.name || getDefaultBaseModel(taskType)
  }

  async function load() {
    loading.value = true
    try {
      const r: any = await autoAnnotateApi.models()
      const items: any[] = r?.models || r || []
      all.value = items
    } catch (e) {
      console.warn('[Training] 加载基础模型列表失败, 回退到默认模型', e)
      all.value = []
    } finally {
      loading.value = false
    }
  }

  return {
    all,
    loading,
    byTask,
    getOptionsFor,
    getDefaultFor,
    load,
  }
}
