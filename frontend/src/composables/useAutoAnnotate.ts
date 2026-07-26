/**
 * useAutoAnnotate.ts
 * ===================================================
 * AI 预标注 composable (v2.5.7 拆分自 Annotate.vue; v2.5.36 接入 SSE 实时进度;
 *                     v2.5.46 +分割预训练分支)
 *
 * 职责:
 * - 分类任务的 AI 预标注 (走 fine-tune / ImageNet 预训练, 同步接口)
 * - 检测任务的 AI 预标注 (走 fine-tune / 预训练 yolov8n/s/m/l/x, Celery 异步 + SSE)
 * - 分割任务的 AI 预标注
 *   · fine-tune: 走 Celery 异步 + SSE (v2.5.36)
 *   · 基础模型 (useFinetune=OFF): 走 torchvision 预训练同步端点 (v2.5.46 新增)
 * - 严格模式: 默认走 fine-tune, 走基础模型前弹窗警告
 *
 * v2.5.36 改造:
 * - detection/segmentation 走 Celery, 入队后订阅 detectionApi.streamProgress /
 *   segmentationApi.streamProgress, 实时显示进度百分比 + message
 * - 终态 (SUCCESS/FAILURE/REVOKED) 自动调用 refreshStats() + loadNext()
 * - autoLabelProgress / autoLabelProgressMessage 暴露给父组件, 用于顶部 toast
 *   / Notification 实时展示
 * - 切页 / 重复点击会自动取消旧的 SSE 连接, 防止僵尸流
 *
 * v2.5.46 新增:
 * - segmentationModelName 入参 (torchvision 预训练名)
 * - runSegmentationPretrained: 同步调 /api/auto-annotate/run-segmentation-pretrained
 *   走 torchvision COCO 21 类预训练, 不经 Celery
 *
 * 依赖:
 * - 入参: datasetId, threshold, iouThreshold, useFinetune, selectedModelId,
 *         modelName, detectionModelName, segmentationModelName, finetuneModels, activeModel
 * - 副作用: refreshStats (父组件定义), loadNext (父组件定义)
 * - 输出: autoLabeling (loading 状态), autoLabelProgress (0-100),
 *         autoLabelProgressMessage (最近一次进度消息)
 */
import { ref, onUnmounted, type Ref } from 'vue'
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
  /** v2.5.46: 分割预训练 (torchvision) 模型名 */
  segmentationModelName: Ref<string>
  finetuneModels: Ref<any[]>
  activeModel: Ref<any>
  refreshStats: () => Promise<void> | void
  loadNext: () => Promise<void> | void
}) {
  const {
    datasetId, threshold, iouThreshold, useFinetune,
    selectedModelId, modelName, detectionModelName, segmentationModelName,
    finetuneModels, activeModel, refreshStats, loadNext,
  } = options

  /** AI 预标注中 (loading 态) */
  const autoLabeling = ref(false)
  /**
   * 实时进度 (0-100), 父组件可绑定到 toolbar / progress 提示上
   * - 入队前/结束后归零
   * - SSE 每帧更新
   */
  const autoLabelProgress = ref(0)
  /** 最近一帧的 message, 父组件可绑定显示 */
  const autoLabelProgressMessage = ref('')
  /** 当前任务 ID, 用于 cancel + 调试 */
  const autoLabelTaskId = ref<string | null>(null)

  /**
   * 当前活动的 SSE 取消器 — 切页/重复点击时主动 cancel, 防止僵尸流
   * (createSSEStream 返回的是一个 () => void, 调用即 abort fetch + ReadableStream)
   */
  let activeStreamCancel: (() => void) | null = null
  const cancelActiveStream = () => {
    if (activeStreamCancel) {
      try { activeStreamCancel() } catch { /* noop */ }
      activeStreamCancel = null
    }
  }
  // 组件卸载时自动 cancel
  onUnmounted(() => {
    cancelActiveStream()
  })

  /**
   * 订阅 SSE 实时进度 — 通用入口, 供 detection / segmentation 复用
   * @param apiCall 'detection' | 'segmentation' — 决定走哪个 api.streamProgress
   * @param taskId Celery task_id
   * @param modelLabel 用于 ElMessage / 终态统计的「模型侧标签」(e.g. yolov8n / fine-tune)
   */
  const subscribeAutoLabelProgress = (
    apiCall: 'detection' | 'segmentation',
    taskId: string,
    modelLabel: string,
  ) => {
    cancelActiveStream()
    autoLabelTaskId.value = taskId
    autoLabelProgress.value = 0
    autoLabelProgressMessage.value = '已入队, 等待 worker 启动...'

    const streamApi = apiCall === 'detection'
      ? detectionApi.streamProgress
      : segmentationApi.streamProgress

    activeStreamCancel = streamApi(taskId, {
      onMessage: (data: any) => {
        const state = data?.state || 'PENDING'
        const progress = Number(data?.progress || 0)
        const message = data?.message || ''
        autoLabelProgress.value = Math.max(0, Math.min(100, progress))
        if (message) autoLabelProgressMessage.value = message

        // 终态 (SUCCESS/FAILURE/REVOKED): 关流 + 收尾
        if (state === 'SUCCESS' || state === 'FAILURE' || state === 'REVOKED') {
          cancelActiveStream()
          autoLabeling.value = false
          handleAutoLabelTerminal(state, modelLabel, data)
        }
      },
      onComplete: () => {
        // 服务端正常 end 事件 → 关流 + 兜底收尾
        // (SUCCESS 帧 onMessage 已处理过, 这里只兜底 FAILURE/REVOKED 等非 SUCCESS 终态)
        cancelActiveStream()
        if (autoLabeling.value) {
          autoLabeling.value = false
          // 兜底拉一次 REST 进度, 避免漏掉消息
          void fetchFinalProgressFallback(apiCall, taskId, modelLabel)
        }
      },
      onError: (err: Error) => {
        // 网络异常 / 后端崩: cancel + 弹错误 + 兜底拉一次 REST
        cancelActiveStream()
        autoLabeling.value = false
        ElMessage.warning(
          `AI 预标注进度推送中断: ${err.message}; 尝试拉取终态...`
        )
        void fetchFinalProgressFallback(apiCall, taskId, modelLabel)
      },
    })
  }

  /**
   * SSE 异常兜底: 拉一次 REST 进度, 拿到终态就刷 stats + loadNext
   * - SSE 在 onComplete / onError 触发后调用
   * - 后端 _resolve_*_task_progress 始终返回最新 state, 即使是终态
   */
  const fetchFinalProgressFallback = async (
    apiCall: 'detection' | 'segmentation',
    taskId: string,
    modelLabel: string,
  ) => {
    try {
      const data: any = apiCall === 'detection'
        ? await detectionApi.progress(taskId)
        : await segmentationApi.progress(taskId)
      handleAutoLabelTerminal(data?.state || 'UNKNOWN', modelLabel, data)
    } catch (e: any) {
      // 拉不到就算了, 至少 refreshStats 让用户看到最新统计
      try { await refreshStats() } catch { /* noop */ }
    }
  }

  /**
   * 终态收尾: 弹消息 + refreshStats + loadNext
   * @param state 'SUCCESS' | 'FAILURE' | 'REVOKED' | 其他
   * @param modelLabel 用于 ElMessage 的模型名 (e.g. yolov8n / fine-tune#42)
   * @param payload 后端返回的完整进度 dict
   */
  const handleAutoLabelTerminal = async (
    state: string,
    modelLabel: string,
    payload: any,
  ) => {
    // 终态统计
    const total = payload?.total ?? payload?.result?.total
    const autoLabeled = payload?.auto_labeled ?? payload?.result?.auto_labeled
    const noMatch = payload?.no_match ?? payload?.result?.no_match

    if (state === 'SUCCESS') {
      if (typeof total === 'number' && typeof autoLabeled === 'number') {
        const nm = typeof noMatch === 'number' ? `, 无匹配 ${noMatch} 张` : ''
        ElMessage.success(
          `[${modelLabel}] AI 预标注完成: 共 ${total} 张, 自动标注 ${autoLabeled} 张${nm}`,
        )
      } else {
        ElMessage.success(`[${modelLabel}] AI 预标注完成`)
      }
    } else if (state === 'FAILURE') {
      const errMsg = payload?.message || payload?.result?.error || '未知错误'
      ElMessage.error(`[${modelLabel}] AI 预标注失败: ${errMsg}`)
    } else if (state === 'REVOKED') {
      ElMessage.warning(`[${modelLabel}] AI 预标注已取消`)
    } else {
      // 兜底: 状态非终态但流断了, 当作未完成处理
      return
    }

    // 终态统一: 刷新统计 + 加载下一张, 让用户立刻看到 AI 标注结果
    try { await refreshStats() } catch { /* noop */ }
    try { await loadNext() } catch { /* noop */ }

    // 重置进度 ref
    autoLabelProgress.value = 0
    autoLabelProgressMessage.value = ''
    autoLabelTaskId.value = null
  }

  /**
   * 启动按钮 dispatcher: 按当前 dataset task_type 分派
   * - 共享 useFinetune state, 由父组件按 dataset 自动隔离
   * - v2.5.46: 分割任务在 useFinetune=OFF 时, 走 torchvision 同步预训练分支
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
      // v2.5.46: 分割基础模型分支走 torchvision 同步端点
      if (!useFinetune.value) {
        await runSegmentationPretrained()
      } else {
        await runSegmentationAutoAnnotate()
      }
    } else {
      ElMessage.warning('未知任务类型: ' + taskType)
    }
  }

  /**
   * 分类任务 AI 预标注 (同步接口, 无 SSE)
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
          // v3.0.0: 展示自动检测不合格的统计 (命中 __unqualified__ 虚拟类别)
          const uqMarked = Number(resp.auto_marked_unqualified || 0)
          const uqHint = uqMarked > 0
            ? `, 自动标记不合格 ${uqMarked} 张`
            : (resp.has_unqualified_class ? ', 未检出不合格' : '')
          ElMessage.success(
            `[Fine-tune ${resp.model_name}] 共 ${resp.total} 张, 命中 ${resp.auto_labeled} 张, 需人工 ${resp.need_human} 张${uqHint}, 平均置信度 ${(resp.avg_confidence * 100).toFixed(1)}%`
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
   * v2.5.36 改造: 入队后订阅 detectionApi.streamProgress, 终态自动 refreshStats + loadNext
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
      let modelLabel: string
      if (useFinetune.value) {
        resp = await detectionApi.startAutoAnnotate({
          dataset_id: datasetId.value,
          model_version_id: selectedModelId.value ?? 0,
          conf_threshold: threshold.value,
          iou_threshold: iouThreshold.value,
        })
        // 选中的 fine-tune 模型名 (找不到就回退到 id)
        const selected = finetuneModels.value.find((m) => m.id === selectedModelId.value)
        const name = selected?.name || resp.model_name || resp.base_model || `model#${selectedModelId.value}`
        modelLabel = `Fine-tune ${name}`
        // 预训练分支用 matched/unmatched 诊断, fine-tune 直接走 SSE
        if (!resp?.task_id) {
          // 后端没返回 task_id, 视为同步完成 (老接口兜底)
          ElMessage.warning('后端未返回 task_id, 无法订阅实时进度')
          return
        }
        // 简短回执 + 启动 SSE
        ElMessage.info(`[${modelLabel}] ${resp.message || '任务已入队, 等待 worker 启动...'}`)
        subscribeAutoLabelProgress('detection', resp.task_id, modelLabel)
        return
      } else {
        resp = await detectionApi.startAutoAnnotatePretrained({
          dataset_id: datasetId.value,
          model_name: detectionModelName.value,
          conf_threshold: threshold.value,
          iou_threshold: iouThreshold.value,
        })
        modelLabel = `预训练 ${detectionModelName.value}`
        if (!resp?.task_id) {
          ElMessage.warning('后端未返回 task_id, 无法订阅实时进度')
          return
        }
      }

      // 下面是预训练分支的诊断 (v2.5.32/33/34)
      const matched = resp.matched_coco_classes || []
      const unmatched = resp.unmatched_categories || []
      const datasetCats = resp.dataset_categories || []
      // v2.5.34: 关键修复 — datasetCats/matched/unmatched 三个诊断字段是「预训练 yolov8n」
      // 端点特有的 (detection.py:483-486), fine-tune 端点 (detection.py:410-414) 不返回这些字段.
      // 之前不管 useFinetune 走哪条路径, 都会无脑读这三个字段, 当 fine-tune 返回 undefined 时
      // datasetCats 会 fall back 到 [], 错误地走"数据集无类目"分支, 误导用户.
      // 修复: 三支诊断只在预训练分支生效; fine-tune 分支直接订阅 SSE, 不做 COCO 诊断.
      if (matched.length === 0 && datasetCats.length > 0) {
        const catList = datasetCats.join('、')
        const unmatchedList = unmatched.length > 0
          ? unmatched.join('、')
          : '(全部未匹配)'
        await ElMessageBox.alert(
          [
            `任务已入队 (task_id: ${resp.task_id || '?'}), 但预训练 ${detectionModelName.value}`,
            `只能识别 COCO 80 类, 与本数据集的 ${datasetCats.length} 个类目均无交集:`,
            '',
            `【本数据集类目】${catList}`,
            '',
            '【未匹配的类目】' + unmatchedList,
            '',
            '【后果】Celery worker 会跑完整个数据集, 但 0 张图会被写入 BBoxAnnotation (空跑)。',
            '',
            '【建议】改用本项目的 fine-tune 模型:',
            '  ① 切回「标注工作台」页',
            '  ② 打开「使用项目训练模型」开关',
            '  ③ 选一个已激活的 fine-tune 模型',
            '  ④ 重新点「启动 AI 预标注」',
            '',
            '(COCO 80 类常见: person, car, cat, dog, bicycle, bird, bottle, chair...)',
          ].join('\n'),
          '预训练模型与项目类目无交集',
          {
            type: 'warning',
            confirmButtonText: '我知道了, 任务继续后台跑',
            dangerouslyUseHTMLString: false,
          }
        )
        // info 提示简短回执
        ElMessage.info(
          `[${modelLabel}] 任务已入队 (task_id: ${resp.task_id || '?'}), ` +
          `但与本数据集 ${datasetCats.length} 个类目均无 COCO 交集, 不会产生标注。`
        )
        // 仍订阅 SSE, 让用户能看到 worker 在跑 (空跑也算跑), 终态会显示 total=0
        subscribeAutoLabelProgress('detection', resp.task_id, modelLabel)
      } else if (datasetCats.length === 0) {
        // v2.5.33: 数据集无类目 — 这是 0 匹配的真凶, 之前会被笼统地归为「未匹配任何 COCO 类」
        // 实际是: 数据集根本没类目, 谈不上匹配 COCO. 弹窗引导用户先去「数据集管理」添加类目
        await ElMessageBox.alert(
          [
            '当前数据集「id=' + (datasetId.value ?? '?') + '」还没有任何类目, 无法进行有效预标注。',
            '',
            '【请按以下步骤添加类目】',
            '  ① 前往「数据集管理」页面',
            '  ② 点击该数据集的「详情」按钮',
            '  ③ 在「类目管理」Tab 中至少添加 1 个类目',
            '  ④ 再回到「标注工作台」启动 AI 预标注',
            '',
            '【原因】预训练 yolov8n 只能识别 COCO 80 类, 推理结果需要映射到项目类目才能写入 BBoxAnnotation. 没有项目类目, 推理结果无处安放, 即使入队也是空跑。',
            '',
            '【建议】如果想测试预标注流程, 可以先在「类目管理」中添加 1-2 个常见 COCO 类目 (如 person, car, cat, dog 等) 再试。',
            '',
            '【任务状态】已入队 (task_id: ' + (resp.task_id || '?') + '), 但因数据集无类目, worker 不会写入任何 BBoxAnnotation。',
          ].join('\n'),
          '数据集无类目',
          {
            type: 'warning',
            confirmButtonText: '我知道了, 任务继续后台跑',
            dangerouslyUseHTMLString: false,
          }
        )
        // info 简短回执
        ElMessage.info(
          `[${modelLabel}] 任务已入队, 但数据集无类目, worker 不会写入任何标注。`
        )
        // 仍订阅 SSE, 让用户能看到 worker 跑 (0 标注) + 终态显示 total/auto_labeled
        subscribeAutoLabelProgress('detection', resp.task_id, modelLabel)
      } else {
        // v2.5.33: matched.length > 0 (部分匹配) — 显示匹配 + 未匹配统计
        // 之前只会显示匹配列表, 用户不知道剩下的类目为什么没匹配
        const unmatchedHint = unmatched.length > 0
          ? `, ${unmatched.length} 个未匹配 (${unmatched.slice(0, 5).join('、')}${unmatched.length > 5 ? '...' : ''})`
          : ''
        ElMessage.info(
          `[${modelLabel}] 任务已入队, 等待 Celery worker 启动... ` +
          `匹配 COCO 类 ${matched.length}/${datasetCats.length}: ${matched.join(', ')}${unmatchedHint}`
        )
        // v2.5.36: 订阅 SSE 实时进度
        subscribeAutoLabelProgress('detection', resp.task_id, modelLabel)
      }
    } catch (e: any) {
      ElMessage.error('AI 预标注失败: ' + (e?.response?.data?.detail || e?.message))
      autoLabeling.value = false
    }
    // 注意: autoLabeling 在 SSE 终态 / onComplete / onError 时统一收尾, 这里不 finally 改回
  }

  /**
   * 分割任务 AI 预标注 (仅走 fine-tune; 分割暂无预训练模型自动标注接口)
   * - 与分类/检测对齐: useFinetune 关闭时弹 warning 阻止, 引导用户切回项目模型
   * - 走后端 POST /api/segmentation/auto-annotate?model_version_id=N&dataset_id=D
   *   (后端 worker: auto_annotate_segmentation_task → predict_to_mask_image → 写 SegmentationMask source=ai)
   * - v2.5.36: 入队后订阅 segmentationApi.streamProgress
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
      const selected = finetuneModels.value.find((m) => m.id === selectedModelId.value)
      const modelName = selected?.name || selected?.base_model || `model#${selectedModelId.value}`
      const modelLabel = `分割 Fine-tune ${modelName}`
      const taskMsg = resp?.task_id
        ? `任务 ID: ${resp.task_id}`
        : (resp?.message || '已入队')
      ElMessage.success(`[${modelLabel}] 任务已入队, 等待 Celery worker 启动... ${taskMsg}`)

      if (!resp?.task_id) {
        // 后端没返回 task_id, 兜底走老的同步刷新
        await refreshStats()
        autoLabeling.value = false
        return
      }
      // v2.5.36: 订阅 SSE 实时进度
      subscribeAutoLabelProgress('segmentation', resp.task_id, modelLabel)
    } catch (e: any) {
      ElMessage.error('AI 预标注失败: ' + (e?.response?.data?.detail || e?.message))
      autoLabeling.value = false
    }
    // 注意: autoLabeling 在 SSE 终态 / onComplete / onError 时统一收尾, 这里不 finally 改回
  }

  /**
   * v2.5.46 新增: 分割任务「基础预标注」(torchvision COCO 21 类)
   * - 走同步端点 POST /api/auto-annotate/run-segmentation-pretrained
   * - 与 runSegmentationAutoAnnotate (走 fine-tune Celery) 互斥
   * - 用户在 AnnotationToolbar 关闭「项目模型」开关时, 启动按钮会走本分支
   * - 后端: backend/app/api/auto_annotate.py:265 (RunSegmentationPretrainedRequest)
   * - 弹窗确认: 沿用检测基础模型的警示文案风格, 提示 COCO 21 类与项目类目的不匹配风险
   */
  const runSegmentationPretrained = async () => {
    if (!datasetId.value) { ElMessage.warning('请先选择数据集'); return }
    try {
      await ElMessageBox.confirm(
        [
          `当前使用「预训练 ${segmentationModelName.value}」(torchvision COCO 21 类).`,
          '仅当数据集类目名与 COCO 类目重合时, 才会写入 SegmentationMask.',
          '建议: 训练项目 fine-tune 模型后再做预标注, 效果更精准.',
          '',
          '是否继续?',
        ].join('\n'),
        '预训练模型预标注确认',
        { confirmButtonText: '继续', cancelButtonText: '取消', type: 'warning' }
      )
    } catch {
      return
    }
    autoLabeling.value = true
    autoLabelProgress.value = 0
    autoLabelProgressMessage.value = '同步推理中 (无 worker, 进度不可见)...'
    try {
      const resp: any = await autoAnnotateApi.runSegmentationPretrained({
        dataset_id: datasetId.value,
        model_name: segmentationModelName.value,
        confidence_threshold: threshold.value,
        crop_size: 256,
      })
      const nm = resp.model_name || segmentationModelName.value
      ElMessage.success(
        `[预训练 ${nm}] 共 ${resp.total} 张, 自动标注 ${resp.auto_labeled} 张, 需人工 ${resp.need_human} 张`,
      )
      await refreshStats()
      await loadNext()
    } catch (e: any) {
      ElMessage.error('AI 预标注失败: ' + (e?.response?.data?.detail || e?.message))
    } finally {
      autoLabeling.value = false
      autoLabelProgress.value = 0
      autoLabelProgressMessage.value = ''
    }
  }

  return {
    autoLabeling,
    autoLabelProgress,
    autoLabelProgressMessage,
    autoLabelTaskId,
    onStartAutoLabelClick,
    runAutoAnnotate,
    runDetectionAutoAnnotate,
    runSegmentationAutoAnnotate,
    /** v2.5.46: 分割预训练 (torchvision) 同步预标注 */
    runSegmentationPretrained,
  }
}
