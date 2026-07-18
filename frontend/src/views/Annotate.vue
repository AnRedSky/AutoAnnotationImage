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
import { Check, Close, Lightning, View, ArrowLeft } from '@element-plus/icons-vue'
import { annotationApi, imageApi, autoAnnotateApi, datasetApi, modelApi } from '@/api'

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
        <el-form-item >
          <!-- 固定宽度容器: 防止 fine-tune / 基础模型 切换时表单 reflow 导致其他控件左右跳动 -->
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
        <el-form-item label="是否使用项目训练模型">
          <!-- 严格模式: 默认开启 fine-tune, 基础模型只作冷启动排查 -->
          <el-switch v-model="useFinetune"
            active-text="是" inactive-text="否"
            inline-prompt style="--el-switch-on-color: #67c23a;" />
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
            <img :src="imageApi.fileUrl(image.id)" alt="待标注"
              style="max-width: 100%; max-height: 480px;" />
            <div style="color: #999; margin-top: 8px; font-size: 13px;">
              <strong>{{ image.filename }}</strong>
              | 尺寸: {{ image.width }}×{{ image.height }}
              | 大小: {{ ((image.file_size || 0) / 1024).toFixed(1) }} KB
            </div>
          </div>
          <el-empty v-else description="暂无待标注图片, 可先点「启动 AI 预标注」批量推理" />
        </el-card>
      </el-col>
      <el-col :span="10">
        <el-card title="AI 候选标签（Top-5）">
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
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.stat-card { text-align: center; }
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
