/**
 * useAnnotationStatusFilter.ts
 * ===================================================
 * 标注工作台 - 图片状态筛选 composable (v3.5.0 增强)
 *
 * 职责:
 * 1. 封装 3 种图片状态的切换逻辑 (待标注 / AI 已标注 / 已人工标注)
 * 2. localStorage 持久化用户的筛选偏好 (跨会话记忆)
 * 3. 暴露「当前是否处于 AI 修正模式」状态, 供子组件用
 * 4. 提供状态 -> 后端 Image.status 取值的映射, 与后端 image_service.py 对齐
 *
 * 状态机 (与后端 Image.status 同步):
 *   pending           待标注
 *   ai_labeled        AI 已标 (待人工修正/确认)
 *   human_confirmed   已确认 (AI 正确)
 *   human_corrected   已修正 (AI 错误, 人工改了)
 *   trained           已参与训练 (不可再改)
 *
 * 设计原则:
 * - 单一职责: 只管筛选状态本身, 不管具体加载逻辑
 * - localStorage 键名独立, 避免与 taskTypeFilter / datasetFilter 冲突
 * - 状态切换时同时清空 session stats, 避免跨 dataset 累计
 */
import { ref, computed, watch } from 'vue'

/** 状态筛选取值 - 4 个值 (3 个主状态 + 1 个 all) */
export type StatusFilterValue = 'pending' | 'ai_labeled' | 'human_labeled' | 'all'

/** 状态 -> 后端 Image.status 映射 (用于 imageApi.list 的 status 参数) */
export const STATUS_FILTER_TO_API: Record<StatusFilterValue, string | string[]> = {
  pending: 'pending',
  ai_labeled: 'ai_labeled',
  // human_labeled 是聚合状态, 后端要查 human_confirmed + human_corrected
  human_labeled: ['human_confirmed', 'human_corrected'],
  all: '',
}

/** 状态 -> 中文标签 */
export const STATUS_FILTER_LABEL: Record<StatusFilterValue, string> = {
  pending: '待标注',
  ai_labeled: 'AI 已标',
  human_labeled: '已人工标注',
  all: '全部',
}

/** localStorage 键名 */
const STORAGE_KEY = 'annotate_status_filter'

/** 从 localStorage 读取初始值, 静默处理读取失败 (隐私模式) */
function loadFromStorage(): StatusFilterValue {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && stored in STATUS_FILTER_LABEL) {
      return stored as StatusFilterValue
    }
  } catch {
    /* localStorage 不可用, 默认 pending */
  }
  return 'pending'
}

export function useAnnotationStatusFilter() {
  const statusFilter = ref<StatusFilterValue>(loadFromStorage())

  /** 持久化: 状态变更时落盘 */
  watch(statusFilter, (v) => {
    try {
      localStorage.setItem(STORAGE_KEY, v)
    } catch {
      /* 静默 */
    }
  })

  /** 派生: 用于 imageApi.list 的 status 入参 */
  const apiStatusParam = computed<string | undefined>(() => {
    const v = STATUS_FILTER_TO_API[statusFilter.value]
    if (Array.isArray(v)) return v.join(',')
    return v || undefined
  })

  /** 派生: 是否处于「AI 修正」模式 (用于子组件判断是否显示 diff 徽章 / 修正按钮) */
  const isAiCorrectionMode = computed(() => statusFilter.value === 'ai_labeled')

  /** 派生: 是否显示「待人工处理」的图 (pending + ai_labeled 都算) */
  const isPendingView = computed(
    () => statusFilter.value === 'pending' || statusFilter.value === 'ai_labeled'
  )

  /** 切换状态 */
  function setStatusFilter(v: StatusFilterValue) {
    statusFilter.value = v
  }

  return {
    statusFilter,
    apiStatusParam,
    isAiCorrectionMode,
    isPendingView,
    setStatusFilter,
  }
}
