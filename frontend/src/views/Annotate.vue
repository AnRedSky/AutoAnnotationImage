<script setup lang="ts">
/**
 * Annotate.vue - 人工标注工作台
 * - 选择数据集 → 显示下一张待标注图
 * - 显示 AI Top-5 候选 + 确认/修正
 * - 实时统计 + 已标注计数
 * - 可跳转到 DatasetDetail 浏览已标注图片
 * - 「使用项目训练模型」开关 ON 时, 显示项目微调模型下拉 (默认=激活的)
 * - 显示当前激活的模型名 + 训练后引导用户到标注页
 */
import { ref, onMounted, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Close, Lightning, View, ArrowLeft, MagicStick, EditPen, Select, Delete, RefreshLeft, RefreshRight } from '@element-plus/icons-vue'
import { annotationApi, imageApi, autoAnnotateApi, datasetApi, modelApi, detectionApi, segmentationApi } from '@/api'
import { getTaskTypeMeta } from '@/utils/taskType'
import DetectionAnnotator from '@/components/annotation/DetectionAnnotator.vue'
import SegmentationAnnotator from '@/components/annotation/SegmentationAnnotator.vue'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const image = ref<any>(null)
const candidates = ref<{ label: string; confidence: number }[]>([])
const startTs = ref(0)
const datasets = ref<any[]>([])
const datasetId = ref<number | null>(null)
const categories = ref<any[]>([])
// base model name (仅 useFinetune=false 时使用)
const modelName = ref('efficientnet_b0')
const threshold = ref(0.6)
const models = ref<any[]>([])         // base models (timm ImageNet)
const finetuneModels = ref<any[]>([]) // 项目训练的 fine-tune models
const selectedModelId = ref<number | null>(null)  // 当前选中的 fine-tune model id
const activeModel = ref<any>(null)    // 当前激活的 model (引导用)
const autoLabeling = ref(false)

// ============== v2.1.0: 检测 / 分割画布数据 ==============
// detection: 已有 bbox 列表 (归一化坐标, 与后端 BBoxAnnotation 一致)
const bboxList = ref<Array<{
  id?: number
  x_min: number; y_min: number; x_max: number; y_max: number
  category_id: number; confidence?: number
}>>([])
// segmentation: 已有 mask 的 URL (后端 /api/segmentation/masks/{id}?download=true 返回的 PNG blob URL)
const initialMaskUrl = ref<string | null>(null)
// segmentation: mask 元信息 (id/width/height), 用于保存时决定 update vs create
const initialMaskMeta = ref<{ id: number; width: number; height: number } | null>(null)
// 标注器保存中 (loading 态)
const annotatorSaving = ref(false)
const stats = ref<any>(null)
const sessionStats = ref({ confirmed: 0, corrected: 0, total_time_ms: 0 })
// 严格模式: 默认使用项目训练的 fine-tune 模型, 严禁默认走基础模型
// (基础模型 ImageNet 输出的 class_532 等不在项目类目, 会被前端归一为「未知」)
const useFinetune = ref(true)

/**
 * 浏览历史栈 (按访问顺序记录看过的 image id, 支持「上一张 / 下一张」双向导航)
 * - historyIds:   所有看过的图片 id 列表
 * - historyCursor: 当前所在位置 (默认 -1, 表示还没加载过)
 *
 * 行为:
 * - 「下一张」: 已在栈顶 → 调后端拉新图 (排除整个 history) 推入栈尾, cursor 移到栈顶
 *             在栈中间 → 直接 cursor++ 拿历史图 (不调后端)
 * - 「上一张」: cursor--, 从 history 直接拿, 调后端 detail 拉最新数据 (状态可能已变)
 * - 标准浏览器行为: 在历史中间点「上一张」再点「下一张」, 应该回到原位置 (不拉新图)
 * - 清空时机: 切换 dataset 时
 */
const historyIds = ref<number[]>([])
const historyCursor = ref(-1)
/**
 * 是否已到末尾 (栈顶时后端 list 返回空, 没有更多待标注图)
 * - true 时「下一张」按钮 disabled
 * - false 时恢复可用 (典型触发: 上一张回到中间 / 启动 AI 预标注完 / 提交标注后)
 */
const noMore = ref(false)

onMounted(async () => {
  try {
    const ds: any = await datasetApi.list()
    datasets.value = ds?.items || ds || []
    if (route.params?.datasetId) {
      datasetId.value = Number(route.params.datasetId)
    } else if (datasets.value.length > 0) {
      datasetId.value = datasets.value[0].id
    }
    // 加载 base models (timm) + 项目 fine-tune models
    const ms: any = await autoAnnotateApi.models()
    models.value = ms?.models || []
    // v2 改造: 默认拉"当前 dataset"的激活模型, 而不是全量 fine-tune 列表
    // 切换 dataset 时 (watch) 也会重新拉该 dataset 的激活模型
    await refreshFinetuneModels()
  } catch (e: any) {
    ElMessage.error('初始化失败: ' + (e?.response?.data?.detail || e?.message))
  }
})

/**
 * v2 改造: 拉取"指定 dataset 已激活的 fine-tune 模型"
 * - 仅显示该 dataset 的激活模型, 与"模型版本管理"的"按数据集显示已激活"语义一致
 * - 若该 dataset 没有任何激活模型, finetuneModels 为空数组, 下拉禁用
 * - 自动选中第一个 (用户可在下拉中切换, 但只有激活的才能选)
 */
const refreshFinetuneModels = async () => {
  const did = datasetId.value
  if (!did) {
    finetuneModels.value = []
    return
  }
  try {
    // 优先用 list + active=true&dataset_id=N 过滤 (与 Models.vue 筛选语义一致)
    const ft: any = await modelApi.list({ dataset_id: did, active: true })
    let items: any[] = ft?.items || ft || []
    // 兜底: 若 list 接口没有按 active 过滤 (旧版本), 再用 getActive 拉一次
    if (items.length === 0) {
      const r: any = await modelApi.getActive(did)
      items = r?.items || (r?.model ? [r.model] : [])
    }
    finetuneModels.value = items
    // 默认选中: 当前已选若仍在列表中, 保留; 否则选第一个; 否则清空
    if (selectedModelId.value && items.find((m) => m.id === selectedModelId.value)) {
      // 保留
    } else if (items.length > 0) {
      selectedModelId.value = items[0].id
    } else {
      selectedModelId.value = null
    }
  } catch {
    finetuneModels.value = []
    selectedModelId.value = null
  }
}

watch(datasetId, async (v) => {
  if (!v) return
  sessionStats.value = { confirmed: 0, corrected: 0, total_time_ms: 0 }
  // 切换 dataset 时, 清空浏览历史 (新 dataset 的 id 集合不同)
  historyIds.value = []
  historyCursor.value = -1
  noMore.value = false
  image.value = null
  candidates.value = []
  try {
    const cats: any = await datasetApi.categories(v)
    categories.value = cats?.items || cats || []
    const s: any = await annotationApi.stats(v)
    stats.value = s
  } catch {}
  // 查询当前 dataset 的激活模型
  try {
    const r: any = await modelApi.getActive(v)
    activeModel.value = r?.items?.[0] || r?.model || null
  } catch {
    activeModel.value = null
  }
  // v2 改造: 切换 dataset 时, 重新拉该 dataset 的激活 fine-tune 模型列表
  // (顶部下拉只显示「该 dataset 已激活」的模型)
  await refreshFinetuneModels()
  // 立即加载第一张
  loadNext()
})

const pendingCount = computed(() => {
  return (stats.value?.status_counts || {}).pending || 0
})

const aiLabeledCount = computed(() => {
  return (stats.value?.status_counts || {}).ai_labeled || 0
})

async function refreshStats() {
  if (!datasetId.value) return
  try {
    const s: any = await annotationApi.stats(datasetId.value)
    stats.value = s
  } catch {}
}

/**
 * 按 id 加载图片并填充 candidates / startTs
 * 不动 historyCursor, 由调用方控制 (loadNext / loadPrev)
 * v2.1.0 新增: 根据 task_type 加载 bbox / mask 已有数据
 */
const fillImage = (item: any) => {
  image.value = item
  const aiPred = item.ai_prediction
  if (aiPred && Array.isArray(aiPred.top5)) {
    // 修复: 后端 ai_service 返回的字段是 confidence, 不是 conf
    candidates.value = aiPred.top5.map((c: any) => ({ label: c.label, confidence: c.confidence }))
  } else {
    candidates.value = []
  }
  startTs.value = Date.now()
  // 重置画布数据
  bboxList.value = []
  if (initialMaskUrl.value) {
    URL.revokeObjectURL(initialMaskUrl.value)
    initialMaskUrl.value = null
  }
  initialMaskMeta.value = null
  // 按 task_type 拉取已有标注
  if (item?.id && item.task_type === 'detection') {
    loadDetectionAnnotations(item.id)
    loadCopySuggestion(item.id)
  } else if (item?.id && item.task_type === 'segmentation') {
    loadSegmentationMask(item.id)
  }
}

/** 加载某图的已有 bbox 列表 */
const loadDetectionAnnotations = async (imageId: number) => {
  try {
    const r: any = await detectionApi.listBBoxes(imageId)
    const items = r?.items || r || []
    bboxList.value = items.map((b: any) => ({
      id: b.id,
      x_min: b.x_min, y_min: b.y_min,
      x_max: b.x_max, y_max: b.y_max,
      category_id: b.category_id,
      confidence: b.confidence,
    }))
  } catch (e: any) {
    bboxList.value = []
    // 静默失败: 没标就是没标
  }
}

// v2.2.0 S9.3: 跨图 bbox 复制建议
const copySuggestions = ref<Array<{
  category_id: number
  avg_x_min: number; avg_y_min: number
  avg_x_max: number; avg_y_max: number
  source_count: number
}>>([])
const copySuggestionSourceCount = ref(0)
const loadCopySuggestion = async (imageId: number) => {
  copySuggestions.value = []
  copySuggestionSourceCount.value = 0
  try {
    const r: any = await detectionApi.copySuggestion(imageId)
    copySuggestions.value = r?.suggestions || []
    copySuggestionSourceCount.value = r?.total_source_images || 0
  } catch (e: any) {
    // 静默失败
  }
}
const applyCopySuggestions = () => {
  if (copySuggestions.value.length === 0) return
  // 追加到 bboxList (避免覆盖已有标注)
  const newBoxes = copySuggestions.value.map((s) => ({
    x_min: s.avg_x_min, y_min: s.avg_y_min,
    x_max: s.avg_x_max, y_max: s.avg_y_max,
    category_id: s.category_id,
  }))
  bboxList.value = [...bboxList.value, ...newBoxes]
  ElMessage.success(`已应用 ${newBoxes.length} 个建议 bbox, 可在画布上微调`)
  copySuggestions.value = []
}
// 类别名查表 (弹窗 tag 用)
function catName(catId: number): string {
  const c = categories.find((x: any) => x.id === catId)
  return c?.name || `cls_${catId}`
}
// v2.3.1 S10: 类别调色板 (与 DetectionAnnotator 一致)
const DET_PALETTE = [
  '#f56c6c', '#67c23a', '#409eff', '#e6a23c',
  '#909399', '#9b59b6', '#1abc9c', '#ff5722',
]
function catColor(catId: number | null | undefined): string {
  if (catId == null) return '#909399'
  return DET_PALETTE[Math.abs(Number(catId)) % DET_PALETTE.length]
}
/** v2.3.1 S10: 取消未保存的修改 (从服务器重读) */
const cancelDetectionDraft = async () => {
  if (!image.value?.id) return
  await loadDetectionAnnotations(image.value.id)
  ElMessage.success('已撤销未保存修改')
}

/** 加载某图的已有 mask (作为初始 mask 渲染到画布) */
const loadSegmentationMask = async (imageId: number) => {
  try {
    // 1) 元数据
    const r: any = await segmentationApi.getMask(imageId)
    if (!r || !r.file_exists || !r.id) {
      initialMaskMeta.value = null
      initialMaskUrl.value = null
      return
    }
    initialMaskMeta.value = { id: r.id, width: r.width, height: r.height }
    // 2) 拉 PNG 二进制 (responseType=blob)
    const resp: any = await segmentationApi.getMask(imageId, true)
    const blob: Blob | null = resp instanceof Blob ? resp
      : (resp?.data instanceof Blob ? resp.data : null)
    if (blob) {
      if (initialMaskUrl.value) URL.revokeObjectURL(initialMaskUrl.value)
      initialMaskUrl.value = URL.createObjectURL(blob)
    } else {
      initialMaskUrl.value = null
    }
  } catch {
    initialMaskMeta.value = null
    initialMaskUrl.value = null
  }
}

/** 保存 detection bbox 列表: clear 旧 + 批量 save 新 */
const saveDetectionBBoxes = async (bboxes: any[]) => {
  if (!image.value?.id) return
  annotatorSaving.value = true
  try {
    // 1) 清空旧 bbox
    await detectionApi.clearBBoxes(image.value.id)
    // 2) 批量写入新 bbox
    for (const b of bboxes) {
      await detectionApi.saveBBox(image.value.id, {
        x_min: b.x_min, y_min: b.y_min,
        x_max: b.x_max, y_max: b.y_max,
        category_id: b.category_id,
        confidence: b.confidence ?? null,
        source: 'human',
      })
    }
    ElMessage.success(`已保存 ${bboxes.length} 个 bbox`)
    // 重新拉一次以同步 id 字段
    await loadDetectionAnnotations(image.value.id)
  } catch (e: any) {
    ElMessage.error('bbox 保存失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    annotatorSaving.value = false
  }
}

/** 保存 segmentation mask: 上传 PNG 文件 (后端自动判断 create vs update) */
const saveSegmentationMask = async (file: File) => {
  if (!image.value?.id) return
  annotatorSaving.value = true
  try {
    await segmentationApi.uploadMask(image.value.id, file, 'human')
    ElMessage.success('mask 已保存')
    await loadSegmentationMask(image.value.id)
  } catch (e: any) {
    ElMessage.error('mask 保存失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    annotatorSaving.value = false
  }
}

/**
 * 「下一张」逻辑:
 * 1. 在历史栈中间 → cursor++ 直接拿历史图, 不发请求 (浏览器行为)
 * 2. 已在栈顶 → 调后端 list (排除整个 history) 拉新图, 推入栈尾
 *    - 若后端无图 (全部 pending 已拿完): 提示"已经是最后一张了", 「下一张」按钮 disabled
 */
const loadNext = async () => {
  if (!datasetId.value) { ElMessage.warning('请先选择数据集'); return }

  // 情况 1: 历史栈中间, 直接前进 (浏览器行为)
  if (historyCursor.value < historyIds.value.length - 1) {
    historyCursor.value++
    const id = historyIds.value[historyCursor.value]
    loading.value = true
    try {
      const detail: any = await imageApi.detail(id)
      fillImage(detail)
    } catch (e: any) {
      ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      loading.value = false
    }
    return
  }

  // 情况 2: 栈顶, 拉新图
  loading.value = true
  try {
    // 排除整个 history (防止连续点下一张回到已看过的图)
    const excludeIdsParam = historyIds.value.length > 0
      ? historyIds.value.join(',')
      : undefined
    const resp: any = await imageApi.list(datasetId.value, {
      status: 'pending',
      page: 1,
      page_size: 1,
      exclude_ids: excludeIdsParam,
    })
    const items = resp?.items || []
    const item = items[0]
    if (!item) {
      // 栈顶 + 后端无新图 = 已到底
      noMore.value = true
      if (historyIds.value.length > 0) {
        ElMessage.warning({
          message: '已经是最后一张了, 没有更多待标注图片。可点击「启动 AI 预标注」让 AI 继续标注。',
          duration: 3500,
          showClose: true,
        })
      } else {
        ElMessage.info('当前数据集没有待标注的图片')
      }
      return
    }
    // 拉到新图, 重置 noMore (用户能看到"还有更多"的信号)
    noMore.value = false
    // 推入历史栈
    historyIds.value.push(item.id)
    historyCursor.value = historyIds.value.length - 1
    fillImage(item)
  } catch (e: any) {
    ElMessage.error('加载失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

/**
 * 「上一张」逻辑:
 * - cursor > 0: cursor--, 从 history 拿图, 调 detail 拉最新数据
 * - cursor = 0: 提示"已经是第一张"
 */
const loadPrev = async () => {
  if (historyCursor.value <= 0) {
    ElMessage.info('已经是第一张了')
    return
  }
  historyCursor.value--
  // 离开栈顶, 重置 noMore (再点下一张时, 栈中间直接拿 history, 不需要重新判断)
  noMore.value = false
  const prevId = historyIds.value[historyCursor.value]
  loading.value = true
  try {
    // 走 detail 拉最新数据 (状态/AI 预测可能已变)
    const detail: any = await imageApi.detail(prevId)
    fillImage(detail)
  } catch (e: any) {
    // 图片可能已被删, 回滚 cursor
    historyIds.value.splice(historyCursor.value, 1)
    historyCursor.value++
    ElMessage.error('加载上一张失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    loading.value = false
  }
}

/** 上一张按钮是否可用 (仅在历史栈非首位时可点) */
const canGoPrev = computed(() => historyCursor.value > 0)

const runAutoAnnotate = async () => {
  if (!datasetId.value) { ElMessage.warning('请先选择数据集'); return }
  // 严格模式: 如果走基础模型分支, 必须先弹窗告知用户结果会被归一为「未知」
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
        { confirmButtonText: '继续用基础模型', cancelButtonText: '切到 Fine-tune', type: 'warning' }
      )
    } catch {
      // 用户取消 → 切到 fine-tune
      useFinetune.value = true
      ElMessage.info('已切换到项目训练模型')
      return
    }
  } else if (useFinetune.value && finetuneModels.value.length === 0) {
    // 冷启动: 没有 fine-tune 模型, 但用户选了 fine-tune 模式
    ElMessage.warning(
      '当前项目还没有训练好的 fine-tune 模型! 请先到「训练任务」页训练一个模型并激活, 再回这里做预标注。'
    )
    return
  }
  autoLabeling.value = true
  try {
    if (useFinetune.value) {
      // 走 /api/images/auto-label/{dataset_id} 用项目训练模型
      const resp: any = await autoAnnotateApi.autoLabel(datasetId.value, {
        confidence_threshold: threshold.value,
        use_finetune: true,
        model_id: selectedModelId.value || undefined,
      })
      // 刷新激活模型 (可能后端回退到默认激活)
      // 注意: getActive 返回 { items: [...] }, 修复前 r?.model 为 undefined 会把激活模型清空
      try {
        const r: any = await modelApi.getActive(datasetId.value)
        const refreshed = r?.items?.[0] || r?.model || null
        // 只在后端真正变更了激活模型时更新, 避免 No pending images 等情况把已有的 activeModel 清掉
        if (refreshed) {
          activeModel.value = refreshed
        }
      } catch {}
      if (resp.used_finetune) {
        ElMessage.success(
          `[Fine-tune ${resp.model_name}] 共 ${resp.total} 张, 命中 ${resp.auto_labeled} 张, 需人工 ${resp.need_human} 张, 平均置信度 ${(resp.avg_confidence * 100).toFixed(1)}%`
        )
      } else if (resp.message) {
        // 后端早 return: 没有 pending 图片 (数据集全部已标)
        // 优先显示用户在下拉框里实际选中的模型名 (与后端实际推理的 model 一致),
        // 回退到 activeModel.name, 最后回退到 resp.model_name, 最后 'Fine-tune'
        const selected = finetuneModels.value.find((m) => m.id === selectedModelId.value)
        const labelName = selected?.name || activeModel.value?.name || resp.model_name || 'Fine-tune'
        ElMessage.info(`[${labelName}] ${resp.message}`)
      } else {
        // 真正的回退: use_finetune=True 但后端找不到 fine-tune → 自动回退到 ImageNet
        ElMessage.warning(
          `[回退 → 基础模型 ${resp.model_name || 'ImageNet'}] ${resp.warning || '当前没有激活的 fine-tune 模型, 已回退到 ImageNet 预训练'}`
        )
      }
    } else {
      // 走 /api/auto-annotate/run 用 timm 预训练 ImageNet (仅在无 fine-tune 时后端才允许)
      const resp: any = await autoAnnotateApi.run({
        dataset_id: datasetId.value,
        model_name: modelName.value,
        confidence_threshold: threshold.value
      })
      ElMessage.warning(
        `[基础模型 ${modelName.value}] 输出已被前端归一为「未知」, 请人工标注. ` +
        `共 ${resp.total} 张, 需人工 ${resp.need_human} 张`
      )
    }
    await refreshStats()
    // AI 预标注后, 部分图被标为 ai_labeled, 剩下的 pending 列表可能变化 → 重置 noMore 让用户重新点「下一张」看
    noMore.value = false
    await loadNext()
  } catch (e: any) {
    ElMessage.error('AI 预标注失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    autoLabeling.value = false
  }
}

const submit = async (labelId: number, labelName: string, isConfirm: boolean) => {
  if (!image.value) return
  const cost = Date.now() - startTs.value
  try {
    await annotationApi.save({
      image_id: image.value.id,
      label_id: labelId,
      time_spent_ms: cost,
      is_confirm: isConfirm
    })
    ElMessage.success(
      `${isConfirm ? '确认' : '修正'}「${labelName}」成功, 耗时 ${cost}ms`
    )
    sessionStats.value.total_time_ms += cost
    if (isConfirm) sessionStats.value.confirmed++
    else sessionStats.value.corrected++
    // 标注成功后重置 noMore: 该图 status 已变, 后端可能返回新的"非当前 history"图
    noMore.value = false
    // 不需要动 historyIds: 该图 status 已变, 下一张「下一张」自然不会再返回
    // (但用「上一张」回看还能再看到, 拿的是最新状态)
    await refreshStats()
    loadNext()
  } catch (e: any) {
    ElMessage.error('保存失败: ' + (e?.response?.data?.detail || e?.message))
  }
}

const viewDataset = () => {
  if (datasetId.value) router.push(`/datasets/${datasetId.value}`)
}

const findCategory = (label: string) => categories.value.find((c) => c.name === label)

// 关键判断: 全部 AI 候选标签都不在项目 category 里 → 等同于基础模型 (ImageNet) 输出
// 整体归一为「未知」, 不再分 5 个独立候选 + 各自置信度
const allUnknown = computed(() => {
  if (!candidates.value.length) return false
  return candidates.value.every((c) => !findCategory(c.label))
})

// 当前选中显示的模型名 (用于 autoLabel 反馈后的 status 提示)
const currentModelLabel = computed(() => {
  if (useFinetune.value) {
    if (selectedModelId.value) {
      const m = finetuneModels.value.find((x) => x.id === selectedModelId.value)
      if (m) return `${m.name} (${m.base_model})`
    }
    return activeModel.value ? `${activeModel.value.name} (激活)` : '未选择 fine-tune 模型'
  }
  return `${modelName.value} (基础模型)`
})

// S7 新增: 当前 dataset 的 task_type 元信息, 用于在顶部展示任务类型徽章
// 找不到 dataset 时回退到 classification, 保持向后兼容
const currentTaskTypeRaw = computed(() => {
  const ds = datasets.value.find((d: any) => d.id === datasetId.value)
  return ds?.task_type || 'classification'
})
// v2.3.0 S10: 检测任务 IoU 阈值 (NMS), 仅 detection 时显示
const iouThreshold = ref(0.45)
</script>

<template>
  <div>
    <!-- 应用训练好的模型引导 -->
    <el-alert
      v-if="activeModel"
      type="success" :closable="false" show-icon
      :title="`当前已激活模型: ${activeModel.name} (基础模型 ${activeModel.base_model}, 准确率 ${(activeModel.accuracy * 100).toFixed(2)}%, 类别数 ${activeModel.num_classes})`"
      style="margin-bottom: 12px;"
    >
      <template #default>
        <div style="margin-top: 4px; font-size: 13px; line-height: 1.6;">
          <strong>如何应用训练好的模型:</strong>
          切换「使用项目训练模型」(是) →
          从下拉框选择本数据集的微调模型 (已激活) →
          点击「启动 AI 预标注」批量推理,
          候选标签会自动对齐到项目预设类目
          ({{ categories.map((c: any) => c.name).join(' / ') || '尚未配置类目' }})。
        </div>
      </template>
    </el-alert>
    <el-alert
      v-else
      type="info" :closable="false" show-icon
      title="当前项目还没有激活的模型"
      style="margin-bottom: 12px;"
    >
      <template #default>
        <div style="margin-top: 4px; font-size: 13px; line-height: 1.6;">
          流程:
          ① 上传数据集 → ② 标注几张图片 → ③ <el-link type="primary" :underline="'never'" @click="router.push('/training')">训练任务</el-link>
          训练首个 fine-tune 模型 → ④ <el-link type="primary" :underline="'never'" @click="router.push('/models')">模型版本</el-link>
          激活 → ⑤ 回到本页, 系统自动选择激活模型做 AI 预标注。
        </div>
      </template>
    </el-alert>

    <!-- 顶部控制条 -->
    <el-card style="margin-bottom: 16px;">
      <el-form inline>
        <el-form-item label="数据集">
          <el-select v-model="datasetId" placeholder="请选择" class="app-select" filterable>
            <el-option v-for="d in datasets" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <!-- S7 新增: 当前 dataset 任务类型徽章 (数据集旁边) -->
        <el-form-item v-if="datasetId" label="任务类型">
          <el-tag :type="currentTaskType.type" effect="plain" size="small">
            {{ currentTaskType.label }}
          </el-tag>
        </el-form-item>
        <!-- v2.3.0 S10: 模型选择区按 task_type 分派
             classification: useFinetune 开关 + fine-tune/基础模型下拉 (原有)
             detection:     IoU 阈值 (新增)
             segmentation:  不显示 -->
        <el-form-item v-if="currentTaskTypeRaw === 'classification'">
          <div class="model-select-slot">
            <!-- fine-tune 模式下: 显示项目训练的微调模型 (默认=激活的) -->
            <el-tooltip
              v-if="useFinetune"
              :content="activeModel ? '当前激活: ' + activeModel.name : '当前没有激活的模型'"
              placement="top">
              <el-select
                v-model="selectedModelId"
                class="app-select"
                :fit-input-width="false"
                popper-class="app-select-dropdown"
                :disabled="finetuneModels.length === 0"
                :placeholder="finetuneModels.length === 0 ? '选择 fine-tune 模型 (仅本数据集已激活)' : '选择 fine-tune 模型'"
              >
              <!-- 风格参考 DatasetDetail.vue 模型下拉:
                   - label 简化为 「name · base_model」, 不再加 ID 前缀
                   - 内部 layout: name (主体) + base_model (灰) + 准确率 (绿, 自动居右)
                   - 移除「激活」绿 tag, 激活状态通过 tooltip 提示 (避免与 Models.vue 产品规范冲突) -->
              <el-option
                v-for="m in finetuneModels" :key="m.id"
                :value="m.id"
                :label="`${m.name} · ${m.base_model}`"
              >
                <div style="display: flex; align-items: center; gap: 6px;">
                  <span>{{ m.name }}</span>
                  <span style="color: #909399; font-size: 12px;">· {{ m.base_model }}</span>
                  <span style="margin-left: auto; color: #67c23a; font-size: 12px;">{{ (m.accuracy * 100).toFixed(1) }}%</span>
                </div>
              </el-option>
            </el-select>
          </el-tooltip>
          <!-- 基础模型模式下: 显示 timm ImageNet 模型 -->
          <el-tooltip
            v-else
            content="基础模型输出会被归一为「未知」, 请谨慎使用"
            placement="top">
            <el-select v-model="modelName" class="app-select" :fit-input-width="false" popper-class="app-select-dropdown">
              <el-option v-for="m in models" :key="m.name" :label="`${m.name} (${m.params})`" :value="m.name">
                <div style="display: flex; align-items: center; gap: 6px;">
                  <el-tag v-if="m.framework" size="small" type="info" effect="plain">{{ m.framework }}</el-tag>
                  <span>{{ m.name }}</span>
                  <span style="color: #909399; font-size: 12px;">({{ m.params }})</span>
                </div>
              </el-option>
            </el-select>
          </el-tooltip>
          </div>
        </el-form-item>
        <el-form-item label="置信度阈值">
          <el-slider v-model="threshold" :min="0.1" :max="1.0" :step="0.05" style="width: 160px;"
            :format-tooltip="(v: number) => `${(v * 100).toFixed(0)}%`" />
        </el-form-item>
        <el-form-item v-if="currentTaskTypeRaw === 'classification'" label="是否使用项目训练模型">
          <!-- 严格模式: 默认开启 fine-tune, 基础模型只作冷启动排查 -->
          <el-switch v-model="useFinetune"
            active-text="是" inactive-text="否"
            inline-prompt style="--el-switch-on-color: #67c23a;" />
        </el-form-item>
        <!-- v2.3.0 S10: 检测任务专属 IoU 阈值 (NMS) -->
        <el-form-item v-if="currentTaskTypeRaw === 'detection'" label="IoU 阈值 (NMS)">
          <el-slider v-model="iouThreshold" :min="0.1" :max="0.95" :step="0.05" style="width: 160px;"
            :format-tooltip="(v: number) => v.toFixed(2)" />
        </el-form-item>
        <el-form-item>
          <el-tooltip
            :content="`当前: ${currentModelLabel}. 启动 AI 预标注会批量推理所有待标注图片, 命中阈值的图自动写入候选标签。`"
            placement="top">
            <el-button type="primary" :icon="Lightning" :loading="autoLabeling" @click="runAutoAnnotate">
              启动 AI 预标注
            </el-button>
          </el-tooltip>
          <el-button :icon="View" @click="viewDataset">查看数据集</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 统计 -->
    <el-row v-if="stats" :gutter="12" style="margin-bottom: 16px;">
      <el-col :span="5">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="待标注" :value="pendingCount" suffix="张"
            :value-style="{ color: '#409eff' }" />
        </el-card>
      </el-col>
      <el-col :span="5">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="AI 已标" :value="aiLabeledCount" suffix="张"
            :value-style="{ color: '#67c23a' }" />
        </el-card>
      </el-col>
      <el-col :span="5">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="本会话已标" :value="sessionStats.confirmed + sessionStats.corrected" suffix="张" />
        </el-card>
      </el-col>
      <el-col :span="5">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="本会话耗时"
            :value="Number((sessionStats.total_time_ms / 1000).toFixed(1))" :precision="1" suffix="秒" />
        </el-card>
      </el-col>
      <el-col :span="4">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="估算 AI 节省" :value="stats.estimated_saved_seconds || 0"
            suffix="秒" :value-style="{ color: '#e6a23c' }" />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="14">
        <el-card :title="image ? `待标注图片 #${image.id}` : '待标注图片'">
          <div v-if="loading" v-loading="true" style="height: 360px;"></div>
          <div v-else-if="image" class="annotate-canvas">
            <!-- v2.1.0: 按 task_type 分派 annotator
                 - detection:    DetectionAnnotator (canvas 拖拽画 bbox)
                 - segmentation: SegmentationAnnotator (canvas 画刷画 mask)
                 - classification: 沿用原 img + AI 候选 (不变) -->
            <template v-if="image.task_type === 'detection'">
              <!-- v2.3.1 S10: DetectionAnnotator 极简版, 操作全在右侧面板 -->
              <DetectionAnnotator
                ref="detAnnotRef"
                :image-url="imageApi.fileUrl(image.id)"
                :image-id="image.id"
                :image-width="image.width || 0"
                :image-height="image.height || 0"
                :categories="categories"
                v-model="bboxList"
                :disabled="annotatorSaving"
                @save="saveDetectionBBoxes"
                @cancel="loadDetectionAnnotations(image.id)"
                @next="loadNext"
                @prev="loadPrev"
              />
            </template>
            <template v-else-if="image.task_type === 'segmentation'">
              <SegmentationAnnotator
                :image-url="imageApi.fileUrl(image.id)"
                :image-id="image.id"
                :image-width="image.width || 0"
                :image-height="image.height || 0"
                :categories="categories"
                :initial-mask-url="initialMaskUrl"
                :disabled="annotatorSaving"
                @save="saveSegmentationMask"
                @cancel="loadSegmentationMask(image.id)"
              />
            </template>
            <template v-else>
              <img :src="imageApi.fileUrl(image.id)" alt="待标注"
                style="max-width: 100%; max-height: 480px;" />
            </template>
            <div style="color: #999; margin-top: 8px; font-size: 13px;">
              <strong>{{ image.filename }}</strong>
              | 尺寸: {{ image.width }}×{{ image.height }}
              | 大小: {{ ((image.file_size || 0) / 1024).toFixed(1) }} KB
              <el-tag v-if="image.task_type" size="small" effect="plain" :type="getTaskTypeMeta(image.task_type).type" style="margin-left: 6px;">
                {{ getTaskTypeMeta(image.task_type).label }}
              </el-tag>
            </div>
          </div>
          <el-empty v-else description="暂无待标注图片, 可先点「启动 AI 预标注」批量推理" />
        </el-card>
      </el-col>
      <el-col :span="10">
        <!-- v2.3.0 S10: 右侧面板按 task_type 分派
             - classification: AI 候选 (原)
             - detection:     操作面板 (跨图建议 + 上一张/下一张 + 进度 + AI 预标注)
             - segmentation:  暂未实现占位 -->
        <el-card v-if="!image || image.task_type === 'classification'" title="AI 候选标签（Top-5）">
          <el-empty v-if="!loading && candidates.length === 0 && !image" description="请选择数据集" :image-size="80" />
          <el-empty v-else-if="candidates.length === 0" description="该图无 AI 预测, 请直接选择其他类别" :image-size="60" />
          <!-- 关键简化: 基础模型 (ImageNet 预训练) 输出 = 全部 Top-5 都不在项目类目
               → 整组归一为「未知」, 不再分 5 个候选 + 各自置信度
               → 强制用户从下方下拉框手动选类目 -->
          <div v-else-if="allUnknown" class="model-confidence-bar"
            style="text-align: center; padding: 32px 12px; border: 1px dashed #f56c6c; border-radius: 6px; background: #fef0f0;">
            <el-tag type="danger" size="large" effect="dark">未知</el-tag>
            <div style="color: #f56c6c; font-size: 13px; margin-top: 12px; line-height: 1.6;">
              AI 基础模型标注信息不在项目类别内<br />
              统一归类为「未知」, 请从下方下拉框手动选择正确类别
            </div>
          </div>
          <div v-for="(c, idx) in candidates" v-show="!allUnknown" :key="`${c.label}-${idx}`" class="model-confidence-bar">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <span>
                <el-tag size="small" type="info">#{{ idx + 1 }}</el-tag>
                <!-- 关键修复: AI 标签若不在项目类目里 (=预训练模型 ImageNet 输出), 强制显示「未知」并禁止采纳 -->
                <strong style="margin-left: 6px;" :class="{ 'unknown-label': !findCategory(c.label) }">
                  {{ findCategory(c.label) ? c.label : '未知' }}
                </strong>
              </span>
              <el-tag :type="c.confidence > 0.8 ? 'success' : c.confidence > 0.5 ? 'warning' : 'info'">
                {{ (c.confidence * 100).toFixed(1) }}%
              </el-tag>
            </div>
            <el-progress :percentage="Math.round(c.confidence * 100)" :show-text="false"
              :color="c.confidence > 0.8 ? '#67c23a' : c.confidence > 0.5 ? '#e6a23c' : '#909399'" />
            <div style="margin-top: 4px;">
              <template v-if="findCategory(c.label)">
                <el-button size="small" type="primary" :icon="Check"
                  @click="submit(findCategory(c.label)!.id, c.label, true)">
                  确认此标签
                </el-button>
                <el-button size="small"
                  @click="submit(findCategory(c.label)!.id, c.label, false)">
                  强制采用
                </el-button>
              </template>
              <!-- 预训练模型输出的标签 (ImageNet class_X / 英文名) 不在项目类目里,
                   视为"未知" — 禁止"确认"和"强制采用", 强制用户从下拉框手动选类目 -->
              <el-tag v-else type="danger" size="small">
                未知（AI 预训练模型输出, 禁止采纳）
              </el-tag>
            </div>
          </div>
          <el-divider v-if="categories.length > 0">或选择其他类别</el-divider>
          <el-select v-if="categories.length > 0" placeholder="选择其他类别（修正）" style="width: 100%;"
            filterable
            @change="(id: number) => {
              const cat = categories.find(c => c.id === id)
              if (cat) submit(cat.id, cat.name, false)
            }"
          >
            <el-option v-for="c in categories" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
          <div style="margin-top: 8px; display: flex; gap: 8px;">
            <el-button
              style="flex: 1;"
              :icon="ArrowLeft"
              :disabled="!canGoPrev"
              @click="loadPrev"
            >上一张</el-button>
            <el-button
              style="flex: 1;"
              :type="noMore ? 'info' : 'danger'"
              :plain="!noMore"
              :icon="Close"
              :disabled="noMore"
              @click="loadNext"
            >{{ noMore ? '已是最后一张' : '下一张' }}</el-button>
          </div>
          <div v-if="noMore" style="margin-top: 6px; font-size: 12px; color: #909399; text-align: center;">
            所有待标注图片已加载完毕，可点击「启动 AI 预标注」继续
          </div>
          <div v-if="datasetId" style="margin-top: 8px; text-align: center;">
            <el-link type="primary" :icon="View" @click="viewDataset">
              去数据集详情浏览全部图片
            </el-link>
          </div>
        </el-card>

        <!-- v2.3.1 S10: 检测任务右侧操作面板 (所有标注相关操作统一在这里) -->
        <el-card v-if="image && image.task_type === 'detection'" title="检测操作面板">
          <!-- Section 1: 工具模式 (绘制/编辑) -->
          <div class="op-section">
            <div class="op-section-title">工具模式</div>
            <el-button-group style="margin-top: 6px; display: flex;">
              <el-button
                style="flex: 1;" :icon="EditPen"
                :type="detAnnot.mode?.value === 'draw' ? 'primary' : 'default'"
                @click="detAnnot.setMode?.('draw')"
              >绘制 (D)</el-button>
              <el-button
                style="flex: 1;" :icon="Select"
                :type="detAnnot.mode?.value === 'edit' ? 'warning' : 'default'"
                @click="detAnnot.setMode?.('edit')"
              >编辑 (E)</el-button>
            </el-button-group>
            <div style="margin-top: 4px; font-size: 11px; color: #909399;">
              <template v-if="detAnnot.mode?.value === 'draw'">在画布上拖拽画新 bbox</template>
              <template v-else>点击选中, 拖动 body 平移, 8 handle 缩放</template>
            </div>
          </div>

          <!-- Section 2: 默认类别 (绘制模式时, 新 bbox 的类别) -->
          <div class="op-section" v-if="detAnnot.mode?.value === 'draw'">
            <div class="op-section-title">新 bbox 类别</div>
            <el-select
              v-model="detAnnot.defaultCategoryId"
              placeholder="选择类别" size="small"
              style="width: 100%; margin-top: 6px;" filterable
            >
              <el-option
                v-for="c in categories" :key="c.id" :value="c.id" :label="c.name"
              >
                <span class="cat-dot" :style="{ background: catColor(c.id) }"></span>
                {{ c.name }}
              </el-option>
            </el-select>
          </div>

          <!-- Section 3: 选中 bbox 的属性 (编辑模式时) -->
          <div class="op-section" v-if="detAnnot.mode?.value === 'edit' && detAnnot.selectedIndex?.value !== null">
            <div class="op-section-title">
              选中 bbox #{{ (detAnnot.selectedIndex.value ?? 0) + 1 }}
            </div>
            <el-select
              :model-value="detAnnot.selectedIndex.value !== null ? bboxList[detAnnot.selectedIndex.value]?.category_id : null"
              @update:model-value="(v: number | null) => detAnnot.changeSelectedCategory?.(v)"
              size="small" style="width: 100%; margin-top: 6px;" filterable
            >
              <el-option
                v-for="c in categories" :key="c.id" :value="c.id" :label="c.name"
              >
                <span class="cat-dot" :style="{ background: catColor(c.id) }"></span>
                {{ c.name }}
              </el-option>
            </el-select>
            <div style="display: flex; gap: 6px; margin-top: 6px;">
              <el-button
                size="small" type="danger" plain style="flex: 1;"
                :icon="Delete"
                @click="detAnnot.removeSelected?.()"
              >删除 (Del)</el-button>
            </div>
          </div>

          <!-- Section 4: 撤销/重做/清空 -->
          <div class="op-section">
            <div class="op-section-title">历史操作</div>
            <div style="display: flex; gap: 6px; margin-top: 6px;">
              <el-button
                size="small" :icon="RefreshLeft" style="flex: 1;"
                :disabled="!detAnnot.canUndo?.value"
                @click="detAnnot.undo?.()"
              >撤销</el-button>
              <el-button
                size="small" :icon="RefreshRight" style="flex: 1;"
                :disabled="!detAnnot.canRedo?.value"
                @click="detAnnot.redo?.()"
              >重做</el-button>
            </div>
            <el-button
              size="small" type="warning" plain style="margin-top: 6px; width: 100%;"
              @click="detAnnot.clearDraft?.()"
            >清空未保存</el-button>
          </div>

          <!-- Section 5: 跨图 bbox 复制建议 -->
          <el-alert
            v-if="copySuggestions.length > 0"
            type="info" :closable="true" show-icon
            style="margin-bottom: 12px;"
            @close="copySuggestions = []"
          >
            <template #title>
              <div style="font-size: 12px; line-height: 1.6;">
                <strong>智能建议</strong>: 基于同数据集 {{ copySuggestionSourceCount }} 张已标注图,
                <strong>{{ copySuggestions.length }}</strong> 个类别可复制
              </div>
            </template>
            <div style="margin-top: 6px;">
              <el-tag
                v-for="s in copySuggestions" :key="s.category_id"
                size="small" type="info" effect="plain"
                style="margin-right: 4px; margin-bottom: 4px;"
              >
                {{ catName(s.category_id) }} ×{{ s.source_count }}
              </el-tag>
              <div style="margin-top: 8px; display: flex; gap: 6px;">
                <el-button
                  type="primary" size="small" :icon="MagicStick"
                  @click="applyCopySuggestions"
                >应用建议</el-button>
                <el-button size="small" text @click="copySuggestions = []">忽略</el-button>
              </div>
            </div>
          </el-alert>

          <!-- Section 6: 图片导航 -->
          <div class="op-section">
            <div class="op-section-title">图片导航</div>
            <div style="display: flex; gap: 8px; margin-top: 6px;">
              <el-button
                style="flex: 1;" :icon="ArrowLeft"
                @click="loadPrev"
              >上一张 (P)</el-button>
              <el-button
                style="flex: 1;"
                :type="noMore ? 'info' : 'primary'"
                :plain="!noMore"
                :disabled="noMore"
                @click="loadNext"
              >{{ noMore ? '已是最后一张' : '下一张 (N)' }}</el-button>
            </div>
            <div v-if="image" style="margin-top: 6px; font-size: 12px; color: #909399; text-align: center;">
              当前: <strong>{{ image.filename }}</strong> · ID #{{ image.id }}
            </div>
          </div>

          <!-- Section 7: 当前 bbox 列表 -->
          <div class="op-section">
            <div class="op-section-title">
              当前 bbox ({{ bboxList.length }})
            </div>
            <el-empty v-if="bboxList.length === 0" description="尚未画任何 bbox" :image-size="50" />
            <div v-else style="margin-top: 6px; max-height: 180px; overflow-y: auto;">
              <el-tag
                v-for="(b, idx) in bboxList" :key="b.id || idx"
                size="small" effect="plain"
                :type="detAnnot.selectedIndex?.value === idx ? 'primary' : 'info'"
                style="margin: 2px 4px 2px 0; cursor: pointer;"
                @click="detAnnot.selectByIndex?.(idx)"
                closable
                @close="removeBBoxAt(idx)"
              >
                <span class="cat-dot" :style="{ background: catColor(b.category_id) }"></span>
                #{{ idx + 1 }} {{ catName(b.category_id) }}
              </el-tag>
            </div>
          </div>

          <!-- Section 8: 保存/取消 -->
          <div class="op-section">
            <div class="op-section-title">提交</div>
            <div style="display: flex; gap: 8px; margin-top: 6px;">
              <el-button
                type="primary" :icon="Check" style="flex: 1;"
                :disabled="!detAnnot.dirty?.value || bboxList.length === 0"
                @click="detAnnot.save?.()"
              >保存 ({{ bboxList.length }})</el-button>
              <el-button
                :icon="Close" style="flex: 1;"
                :disabled="!detAnnot.dirty?.value"
                @click="cancelDetectionDraft"
              >取消</el-button>
            </div>
            <div v-if="detAnnot.dirty?.value" style="margin-top: 4px; font-size: 11px; color: #e6a23c;">
              ● 有未保存的修改
            </div>
          </div>

          <!-- 任务专属提示 -->
          <div class="op-section">
            <el-link type="primary" :icon="View" @click="viewDataset">
              去数据集详情浏览全部图片
            </el-link>
          </div>
        </el-card>

        <!-- v2.3.0 S10: 分割任务占位 -->
        <el-card v-else-if="image && image.task_type === 'segmentation'" title="分割操作面板">
          <el-empty description="分割任务操作面板待 S9.5 完善" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.stat-card { text-align: center; }
/* v2.3.0 S10: 检测操作面板 section 样式 */
.op-section {
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px dashed #ebeef5;
}
.op-section:last-child {
  margin-bottom: 0;
  padding-bottom: 0;
  border-bottom: none;
}
.op-section-title {
  font-size: 12px;
  font-weight: 600;
  color: #606266;
  letter-spacing: 0.5px;
  display: flex;
  align-items: center;
}
.op-section-title::before {
  content: '';
  display: inline-block;
  width: 3px;
  height: 12px;
  background: #409eff;
  margin-right: 6px;
  border-radius: 2px;
}
.annotate-canvas {
  display: flex;
  flex-direction: column;
  align-items: center;
}
/* 模型下拉切换容器: 固定宽度, 防止 useFinetune 切换时表单 reflow 抖动 */
.model-select-slot {
  display: inline-block;
  width: 260px;
}
.model-confidence-bar {
  padding: 10px 0;
  border-bottom: 1px dashed #ebeef5;
}
.model-confidence-bar:last-of-of { border-bottom: none; }
.unknown-label {
  color: #f56c6c;
  font-style: italic;
  font-weight: 600;
}
</style>
