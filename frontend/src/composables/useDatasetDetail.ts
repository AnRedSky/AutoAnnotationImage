import { ref, computed, watch, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { datasetApi, imageApi, statsApi, modelApi, exportApi } from '@/api'
import type { TaskType } from '@/utils/taskType'

/**
 * useDatasetDetail - 数据集详情页核心数据加载与导航
 *
 * v3.0.0 Phase L 拆分: 从 DatasetDetail/index.vue 抽离
 *
 * 职责:
 * - 数据集基本信息 + 图像列表 + 统计数据加载
 * - 类目列表 (供类别数卡 hover 详情)
 * - 激活模型 / fine-tune 模型列表
 * - 分页 + 状态过滤 + 关键词
 * - 导出 (COCO/YOLO/CSV)
 * - 跳转 (返回列表 / 跳标注工作台, 带 imageId query)
 *
 * 父组件通过 expose 出去的 ref 访问, 操作通过回调触发
 */
export interface UseDatasetDetailOptions {
  /** 是否自动加载 (onMounted 时调用) */
  autoLoad?: boolean
}

export function useDatasetDetail(options: UseDatasetDetailOptions = {}) {
  const route = useRoute()
  const router = useRouter()
  const { autoLoad = true } = options

  // ============== 路由派生的 datasetId ==============
  const datasetId = computed(() => Number(route.params.id))

  // 同样暴露给父组件使用 (用于 UploadQueue 等需要纯数字 id 的子组件)
  const datasetIdRef = computed(() => datasetId.value)

  // ============== 核心状态 ==============
  const dataset = ref<any>(null)
  const images = ref<any[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(24)
  const pageSizes = ref([12, 24, 48, 96])
  const statusFilter = ref<string>('all')   // 'all' = 全部
  const keyword = ref('')
  const loading = ref(false)
  const stats = ref<any>(null)
  const categories = ref<any[]>([])
  const activeModel = ref<any>(null)

  // ============== 选项 (从 page 迁移过来) ==============
  const statusOptions = [
    { value: 'all', label: '全部', color: '#909399' },
    { value: 'pending', label: '待标注', color: '#909399' },
    { value: 'ai_labeled', label: 'AI 已标', color: '#409eff' },
    { value: 'human_confirmed', label: '已确认', color: '#67c23a' },
    { value: 'human_corrected', label: '已修正', color: '#e6a23c' },
  ]

  // ============== 过滤后图像 (关键词筛选) ==============
  const filteredImages = computed(() => {
    if (!keyword.value.trim()) return images.value
    const kw = keyword.value.toLowerCase()
    return images.value.filter((img) =>
      (img.filename || '').toLowerCase().includes(kw) ||
      (img.final_label_name || '').toLowerCase().includes(kw) ||
      (img.ai_prediction?.top1 || '').toLowerCase().includes(kw)
    )
  })

  // ============== "待标注" 图片数 (AI 预标注输入目标) ==============
  const pendingCount = computed(
    () => images.value.filter((img) => img.status === 'pending').length
  )

  // ============== 任务类型 (供其他组件消费) ==============
  const currentDatasetTaskType = computed<TaskType>(() => {
    const tt = (dataset.value?.task_type as string) || 'classification'
    if (tt === 'detection' || tt === 'segmentation') return tt
    return 'classification'
  })

  // ============== 数据加载 (核心) ==============
  async function load() {
    if (!datasetId.value) return
    loading.value = true
    try {
      // v2.5.49: 并行拉取 categories (供「类别数」卡 hover 详情)
      const [d, list, s, actResp, catsResp]: any[] = await Promise.all([
        datasetApi.get(datasetId.value),
        imageApi.list(datasetId.value, {
          status: statusFilter.value === 'all' ? undefined : statusFilter.value,
          page: page.value,
          page_size: pageSize.value,
        }),
        statsApi.dataset(datasetId.value).catch(() => null),
        modelApi.getActive(datasetId.value).catch(() => ({ model: null })),
        datasetApi.categories(datasetId.value).catch(() => null),
      ])
      dataset.value = d
      images.value = list?.items || []
      total.value = list?.total || 0
      stats.value = s
      activeModel.value = actResp?.model || actResp?.items?.[0] || null
      const c: any = catsResp
      categories.value = c?.items || c || []
    } catch (e: any) {
      ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      loading.value = false
    }
  }

  // ============== 分页与筛选变更 ==============
  function resetPage() {
    page.value = 1
    load()
  }
  function changePage(p: number) {
    page.value = p
    load()
  }
  function onSizeChange(s: number) {
    pageSize.value = s
    page.value = 1
    load()
  }

  // ============== 状态过滤变更 -> 自动重置分页 ==============
  watch(statusFilter, () => {
    page.value = 1
    load()
  })
  // 切换数据集时重置分页, 避免停留在旧数据集的高页码导致空列表
  watch(() => route.params.id, () => {
    page.value = 1
    load()
  })

  // ============== 导航 ==============
  function goBack() { router.push('/datasets') }

  /**
   * v2.5.44: 跳转标注工作台时, 若 selectedIds 非空, 把第一张待标注图 id 作为 imageId query
   * - Annotate 页 onMounted / watch(datasetId) 会读这个 query, 把该图设为当前图
   * - 用户进入工作台后看到的第一张, 必然是自己勾选的那批里的「待标注」第一张
   * - 继续点「下一张」时, 工作台按原有逻辑从后端拉新图
   */
  function goAnnotate(selectedIds: Ref<number[]>) {
    const did = datasetId.value
    if (!did) return
    let targetImageId: number | null = null
    if (selectedIds.value.length > 0) {
      const sel = new Set(selectedIds.value)
      const firstPending = images.value.find(
        (img: any) => sel.has(img.id) && img.status === 'pending'
      )
      if (firstPending) targetImageId = firstPending.id
    }
    if (targetImageId != null) {
      router.push({ path: `/annotate/${did}`, query: { imageId: String(targetImageId) } })
    } else {
      router.push(`/annotate/${did}`)
    }
  }

  // ============== 导出 (COCO / YOLO / CSV) ==============
  async function handleExport(format: 'coco' | 'yolo' | 'csv') {
    const url = exportApi[format](datasetId.value)
    const token = localStorage.getItem('token')
    try {
      const r = await fetch(url, { headers: { Authorization: `Bearer ${token}` } })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const blob = await r.blob()
      const ext = format === 'yolo' ? 'zip' : format === 'coco' ? 'json' : 'csv'
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `${dataset.value?.name || 'dataset'}_${datasetId.value}.${ext}`
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (e: any) {
      ElMessage.error('导出失败: ' + e?.message)
    }
  }

  // ============== 工具函数 (image 状态/置信度色) ==============
  function formatBytes(b: number): string {
    if (!b) return '-'
    if (b < 1024) return `${b} B`
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
    return `${(b / 1024 / 1024).toFixed(2)} MB`
  }
  function confColor(c: number): string {
    if (c >= 0.8) return '#67c23a'
    if (c >= 0.5) return '#e6a23c'
    return '#909399'
  }
  function statusType(s: string): 'success' | 'warning' | 'info' | 'primary' | 'danger' {
    const t: Record<string, 'success' | 'warning' | 'info' | 'primary' | 'danger'> = {
      pending: 'info',
      ai_labeled: 'primary',
      human_confirmed: 'success',
      human_corrected: 'warning',
      trained: 'success',
    }
    return t[s] || 'info'
  }
  function statusLabel(s: string): string {
    return {
      pending: '待标注',
      ai_labeled: 'AI',
      human_confirmed: '已确认',
      human_corrected: '已修正',
      trained: '已训练',
    }[s] || s
  }

  /**
   * 判断图片是否"有任何标注" (用于「去标」按钮可见性)
   * v2.5.17 修复: 之前只看 image.status + final_label_id, 漏掉
   *   - detection 图: image.status 可能停留在 pending/ai_labeled, 但已有 BBoxAnnotation 行
   *   - segmentation 图: 同上, SegmentationMask 已存在但 image.status 没更新
   * 现在 OR 上后端新返回的 bbox_count (检测) / has_mask (分割), 即使老数据
   *   status 未更新, 只要 DB 有真实标注就显示「去标」按钮
   */
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

  // ============== 自动加载 ==============
  if (autoLoad) {
    load()
  }

  return {
    // 路由
    datasetId, datasetIdRef,
    // 状态
    dataset, images, total, page, pageSize, pageSizes,
    statusFilter, keyword, loading, stats, categories, activeModel,
    // 选项
    statusOptions,
    // 计算属性
    filteredImages, pendingCount, currentDatasetTaskType,
    // 数据加载
    load, resetPage, changePage, onSizeChange,
    // 导航
    goBack, goAnnotate,
    // 导出
    handleExport,
    // 工具函数
    formatBytes, confColor, statusType, statusLabel, hasAnnotation,
  }
}
