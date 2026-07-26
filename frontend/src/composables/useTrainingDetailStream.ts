/**
 * useTrainingDetailStream - 训练详情 SSE 流 + 数据集统计 + 历史曲线
 *
 * v3.0.0 Phase J 拆分: 从 Training/index.vue 抽离
 * - 详情打开 (openDetail): 拉 DB → 拉历史日志 → 连 SSE → 定时拉 history
 * - 详情关闭 (cleanupDetail): 断开 SSE + 清理 echarts + 清理定时器
 * - SSE 帧处理: 同步 state/progress/epoch/started_at/finished_at + 增量训练状态
 * - 终态检测: 重新拉 DB 拿最终 finished_at / duration / device_info
 *
 * 数据集统计双源 fallback (SSE 实时 ref + DB 持久化):
 * - displayDataTotal / displayDataTrain / displayDataVal / displayNumClasses / displayClassNames
 *   computed 优先取 SSE 推的 ref, null 时回退到 detailJob 的 DB 字段
 */
import { ref, computed, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { trainingApi } from '@/api'

export interface EpochData {
  epoch: number
  train_loss: number
  val_loss: number
  train_acc: number
  val_acc: number
  // 检测
  box_loss?: number
  cls_loss?: number
  dfl_loss?: number
  map_50?: number
  map_50_95?: number
  precision?: number
  recall?: number
  // 分割
  miou?: number
  pixel_acc?: number
  dice?: number
}

export function useTrainingDetailStream() {
  // ============== 状态 ==============
  const visible = ref(false)
  const job = ref<any>(null)
  const error = ref<any>(null)
  const progress = ref(0)
  const state = ref('PENDING')
  const currentEpoch = ref<number | null>(null)
  const totalEpochs = ref<number | null>(null)
  const message = ref('')
  const log = ref<string[]>([])
  const history = ref<EpochData[]>([])
  const chartEl = ref<HTMLDivElement>()
  // 数据集统计
  const dataTotal = ref<number | null>(null)
  const dataTrain = ref<number | null>(null)
  const dataVal = ref<number | null>(null)
  const numClasses = ref<number | null>(null)
  const classNames = ref<string[]>([])
  // 增量训练状态
  const pretrainedLoaded = ref<boolean | null>(null)
  const pretrainedPath = ref<string | null>(null)
  const pretrainedError = ref<string | null>(null)

  // ---- 数据集统计双源 fallback (SSE 实时 ref + DB 持久化) ----
  const displayDataTotal = computed(() => dataTotal.value ?? job.value?.data_total ?? null)
  const displayDataTrain = computed(() => dataTrain.value ?? job.value?.data_train ?? null)
  const displayDataVal = computed(() => dataVal.value ?? job.value?.data_val ?? null)
  const displayNumClasses = computed(() => numClasses.value ?? job.value?.num_classes ?? null)
  const displayClassNames = computed(() => {
    if (classNames.value.length > 0) return classNames.value
    if (Array.isArray(job.value?.class_names) && job.value.class_names.length > 0) {
      return job.value.class_names
    }
    return []
  })

  // ---- 内部: SSE / chart / timer 句柄 ----
  let chart: echarts.ECharts | null = null
  let cancelStream: (() => void) | null = null
  let historyTimer: any = null
  let lastLogSaveAt = 0

  // ============== 持久化一行训练日志到后端 ==============
  const saveDetailLog = (taskId: string, line: string) => {
    const jobId = job.value?.id
    if (!jobId) return
    const now = Date.now()
    if (now - lastLogSaveAt < 500) return  // 500ms 节流
    lastLogSaveAt = now
    trainingApi.appendLog(jobId, line).catch(() => {
      // 静默失败: 持久化是 best-effort, 训练过程不受影响
    })
  }

  // ============== SSE 流 ==============
  const startStream = (taskId: string) => {
    log.value.push(
      `[${new Date().toLocaleTimeString()}] 已连接 SSE 进度推送, task_id=${taskId.slice(0, 8)}…`
    )
    cancelStream = trainingApi.streamProgress(taskId, {
      onMessage: (data) => {
        // 同步 detailState 和 job.state (修复「下方完成, 上方还显示训练中」的 bug)
        const newState = data.state || 'PROGRESS'
        state.value = newState
        if (job.value) job.value.state = newState
        progress.value = Number(data.progress || 0)
        currentEpoch.value = data.current_epoch ?? null
        totalEpochs.value = data.total_epochs ?? totalEpochs.value
        message.value = data.message || message.value
        // 实时同步 started_at / finished_at (宽松比较: 转时间戳比秒级精度, 避免 ISO 格式微差异)
        if (data.started_at && job.value) {
          const _tsNew = new Date(data.started_at).getTime()
          const _tsOld = job.value.started_at ? new Date(job.value.started_at).getTime() : 0
          if (_tsNew && Math.abs(_tsNew - _tsOld) > 1000) {
            job.value.started_at = String(data.started_at)
          }
        }
        if (data.finished_at && job.value) {
          const _tsNew = new Date(data.finished_at).getTime()
          const _tsOld = job.value.finished_at ? new Date(job.value.finished_at).getTime() : 0
          if (_tsNew && Math.abs(_tsNew - _tsOld) > 1000) {
            job.value.finished_at = String(data.finished_at)
          }
        }
        // 数据集统计
        if (typeof data.data_total === 'number') dataTotal.value = data.data_total
        if (typeof data.data_train === 'number') dataTrain.value = data.data_train
        if (typeof data.data_val === 'number') dataVal.value = data.data_val
        if (typeof data.num_classes === 'number') numClasses.value = data.num_classes
        if (Array.isArray(data.class_names)) classNames.value = data.class_names
        // 增量训练状态
        if (typeof data.pretrained_loaded === 'boolean') {
          pretrainedLoaded.value = data.pretrained_loaded
        }
        if (typeof data.pretrained_path === 'string') pretrainedPath.value = data.pretrained_path
        if (typeof data.pretrained_error === 'string') pretrainedError.value = data.pretrained_error

        const logLine =
          `[${new Date().toLocaleTimeString()}] state=${data.state} ` +
          `progress=${(Number(data.progress || 0)).toFixed(1)}% ` +
          `epoch=${data.current_epoch ?? '-'}/${data.total_epochs ?? '-'} ` +
          `msg=${data.message || ''}`
        log.value.push(logLine)
        if (log.value.length > 100) log.value = log.value.slice(-100)
        saveDetailLog(taskId, logLine)

        // 终态检测: 重新拉完整 job 拿 finished_at / duration / device_info
        const isTerminal = ['SUCCESS', 'FAILURE', 'REVOKED'].includes(newState)
        if (isTerminal && job.value?.id) {
          trainingApi.job(job.value.id).then((d: any) => {
            job.value = d
          }).catch(() => {
            // 拉取失败不影响其他字段, 保留 SSE 已推的 state
          })
        }
      },
      onComplete: () => {
        cancelStream = null
        if (historyTimer) { clearInterval(historyTimer); historyTimer = null }
        refreshHistory(taskId)
        if (job.value?.id) {
          trainingApi.job(job.value.id).then((d: any) => {
            job.value = d
          }).catch(() => { /* 拉取失败 */ })
        }
      },
      onError: (e) => {
        cancelStream = null
        // v3.0.0 修复: SSE 断开时也必须清 historyTimer, 否则 5s 轮询永久运行
        // (之前只在 onComplete 清, 网络异常断连时 historyTimer 泄漏)
        if (historyTimer) { clearInterval(historyTimer); historyTimer = null }
        log.value.push(`[${new Date().toLocaleTimeString()}] SSE 断开: ${e.message}`)
      },
    })
  }

  const refreshHistory = async (taskId: string) => {
    try {
      const h: any = await trainingApi.history(taskId)
      history.value = h?.history || []
    } catch { /* 拉取失败不影响主流程 */ }
  }

  // ============== ECharts 训练曲线 ==============
  const initChart = () => {
    if (!chartEl.value) return
    chart = echarts.init(chartEl.value)
    const isDetection = job.value?.task_type === 'detection'
    const isSegmentation = job.value?.task_type === 'segmentation'
    if (isDetection) {
      chart.setOption({
        title: { text: '检测训练曲线 (Loss / mAP / P / R)', left: 'center', textStyle: { fontSize: 14, fontWeight: 600, color: '#1f2937' } },
        tooltip: { trigger: 'axis' },
        legend: { data: ['box_loss', 'cls_loss', 'mAP50', 'mAP50-95', 'precision', 'recall'], top: 30, type: 'scroll', textStyle: { color: '#6b7280' } },
        grid: { top: 80, left: 55, right: 55, bottom: 40, containLabel: true },
        xAxis: { type: 'category', name: 'Epoch', data: [], axisLine: { lineStyle: { color: '#d6d8de' } } },
        yAxis: [
          { type: 'value', name: 'Loss', position: 'left', axisLine: { lineStyle: { color: '#4f7cff' } }, splitLine: { lineStyle: { color: '#eef0f4' } } },
          { type: 'value', name: 'mAP / P / R', position: 'right', min: 0, max: 1, axisLine: { lineStyle: { color: '#00c48c' } }, splitLine: { show: false } },
        ],
        series: [
          { name: 'box_loss', type: 'line', yAxisIndex: 0, data: [], smooth: true, lineStyle: { color: '#4f7cff', width: 2 }, itemStyle: { color: '#4f7cff' }, symbolSize: 6 },
          { name: 'cls_loss', type: 'line', yAxisIndex: 0, data: [], smooth: true, lineStyle: { color: '#ff8a4c', width: 2 }, itemStyle: { color: '#ff8a4c' }, symbolSize: 6 },
          { name: 'mAP50', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#00c48c', width: 2 }, itemStyle: { color: '#00c48c' }, symbolSize: 6 },
          { name: 'mAP50-95', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#722ed1', width: 2 }, itemStyle: { color: '#722ed1' }, symbolSize: 6 },
          { name: 'precision', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#13c2c2', width: 2 }, itemStyle: { color: '#13c2c2' }, symbolSize: 6 },
          { name: 'recall', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#fa541c', width: 2 }, itemStyle: { color: '#fa541c' }, symbolSize: 6 },
        ],
      })
    } else if (isSegmentation) {
      chart.setOption({
        title: { text: '分割训练曲线 (Loss / mIoU / Pixel Acc / Dice)', left: 'center', textStyle: { fontSize: 14, fontWeight: 600, color: '#1f2937' } },
        tooltip: { trigger: 'axis' },
        legend: { data: ['train_loss', 'val_loss', 'mIoU', 'pixel_acc', 'dice'], top: 30, type: 'scroll', textStyle: { color: '#6b7280' } },
        grid: { top: 80, left: 55, right: 55, bottom: 40, containLabel: true },
        xAxis: { type: 'category', name: 'Epoch', data: [], axisLine: { lineStyle: { color: '#d6d8de' } } },
        yAxis: [
          { type: 'value', name: 'Loss', position: 'left', axisLine: { lineStyle: { color: '#4f7cff' } }, splitLine: { lineStyle: { color: '#eef0f4' } } },
          { type: 'value', name: 'mIoU / Acc / Dice', position: 'right', min: 0, max: 1, axisLine: { lineStyle: { color: '#00c48c' } }, splitLine: { show: false } },
        ],
        series: [
          { name: 'train_loss', type: 'line', yAxisIndex: 0, data: [], smooth: true, lineStyle: { color: '#4f7cff', width: 2 }, itemStyle: { color: '#4f7cff' }, symbolSize: 6 },
          { name: 'val_loss', type: 'line', yAxisIndex: 0, data: [], smooth: true, lineStyle: { color: '#ff8a4c', width: 2 }, itemStyle: { color: '#ff8a4c' }, symbolSize: 6 },
          { name: 'mIoU', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#00c48c', width: 2 }, itemStyle: { color: '#00c48c' }, symbolSize: 6 },
          { name: 'pixel_acc', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#722ed1', width: 2 }, itemStyle: { color: '#722ed1' }, symbolSize: 6 },
          { name: 'dice', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#13c2c2', width: 2 }, itemStyle: { color: '#13c2c2' }, symbolSize: 6 },
        ],
      })
    } else {
      chart.setOption({
        title: { text: '训练曲线 (Loss / Accuracy)', left: 'center', textStyle: { fontSize: 14, fontWeight: 600, color: '#1f2937' } },
        tooltip: { trigger: 'axis' },
        legend: { data: ['train_loss', 'val_loss', 'train_acc', 'val_acc'], top: 30, textStyle: { color: '#6b7280' } },
        grid: { top: 80, left: 50, right: 50, bottom: 40, containLabel: true },
        xAxis: { type: 'category', name: 'Epoch', data: [], axisLine: { lineStyle: { color: '#d6d8de' } } },
        yAxis: [
          { type: 'value', name: 'Loss', position: 'left', axisLine: { lineStyle: { color: '#4f7cff' } }, splitLine: { lineStyle: { color: '#eef0f4' } } },
          { type: 'value', name: 'Accuracy', position: 'right', min: 0, max: 1, axisLine: { lineStyle: { color: '#00c48c' } }, splitLine: { show: false } },
        ],
        series: [
          { name: 'train_loss', type: 'line', yAxisIndex: 0, data: [], smooth: true, lineStyle: { color: '#4f7cff', width: 2 }, itemStyle: { color: '#4f7cff' }, symbolSize: 6 },
          { name: 'val_loss', type: 'line', yAxisIndex: 0, data: [], smooth: true, lineStyle: { color: '#ff8a4c', width: 2 }, itemStyle: { color: '#ff8a4c' }, symbolSize: 6 },
          { name: 'train_acc', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#00c48c', width: 2 }, itemStyle: { color: '#00c48c' }, symbolSize: 6 },
          { name: 'val_acc', type: 'line', yAxisIndex: 1, data: [], smooth: true, lineStyle: { color: '#722ed1', width: 2 }, itemStyle: { color: '#722ed1' }, symbolSize: 6 },
        ],
      })
    }
    window.addEventListener('resize', () => chart?.resize())
  }

  // 监听 history 变化, 重绘图表
  const updateChartFromHistory = () => {
    if (!chart || history.value.length === 0) return
    const h = history.value
    const isDetection = job.value?.task_type === 'detection'
    const isSegmentation = job.value?.task_type === 'segmentation'
    if (isDetection) {
      chart.setOption({
        xAxis: { data: h.map((x) => x.epoch) },
        series: [
          { data: h.map((x) => +(x.box_loss ?? 0).toFixed(4)) },
          { data: h.map((x) => +(x.cls_loss ?? 0).toFixed(4)) },
          { data: h.map((x) => +(x.map_50 ?? 0).toFixed(4)) },
          { data: h.map((x) => +(x.map_50_95 ?? 0).toFixed(4)) },
          { data: h.map((x) => +(x.precision ?? 0).toFixed(4)) },
          { data: h.map((x) => +(x.recall ?? 0).toFixed(4)) },
        ],
      })
    } else if (isSegmentation) {
      chart.setOption({
        xAxis: { data: h.map((x) => x.epoch) },
        series: [
          { data: h.map((x) => +((x.train_loss ?? 0)).toFixed(4)) },
          { data: h.map((x) => +((x.val_loss ?? 0)).toFixed(4)) },
          { data: h.map((x) => +((x.miou ?? 0)).toFixed(4)) },
          { data: h.map((x) => +((x.pixel_acc ?? 0)).toFixed(4)) },
          { data: h.map((x) => +((x.dice ?? 0)).toFixed(4)) },
        ],
      })
    } else {
      chart.setOption({
        xAxis: { data: h.map((x) => x.epoch) },
        series: [
          { data: h.map((x) => +x.train_loss.toFixed(4)) },
          { data: h.map((x) => +x.val_loss.toFixed(4)) },
          { data: h.map((x) => +x.train_acc.toFixed(4)) },
          { data: h.map((x) => +x.val_acc.toFixed(4)) },
        ],
      })
    }
  }

  // ============== 打开详情 ==============
  const openDetail = async (row: any) => {
    cleanup()
    job.value = { ...row }
    error.value = null
    progress.value = Number(row.progress || 0)
    state.value = row.state || 'PENDING'
    currentEpoch.value = null
    totalEpochs.value = row.epochs
    message.value = row.message || ''
    log.value = []
    history.value = []
    dataTotal.value = null
    dataTrain.value = null
    dataVal.value = null
    numClasses.value = null
    classNames.value = []
    pretrainedLoaded.value = null
    pretrainedPath.value = null
    pretrainedError.value = null
    visible.value = true

    // 取一次最新详情 (含 error + 从 DB 回填数据集统计)
    try {
      const d: any = await trainingApi.job(row.id)
      job.value = d
      progress.value = Number(d.progress || 0)
      state.value = d.state
      totalEpochs.value = d.epochs
      message.value = d.message || ''
      if (typeof d.data_total === 'number') dataTotal.value = d.data_total
      if (typeof d.data_train === 'number') dataTrain.value = d.data_train
      if (typeof d.data_val === 'number') dataVal.value = d.data_val
      if (typeof d.num_classes === 'number') numClasses.value = d.num_classes
      if (Array.isArray(d.class_names)) classNames.value = d.class_names
      if (d.state === 'FAILURE' || d.error) {
        try {
          const e: any = await trainingApi.error(row.id)
          error.value = e
        } catch { /* 拉取失败不影响主流程 */ }
      }
    } catch (e: any) {
      ElMessage.warning('获取任务详情失败: ' + (e?.response?.data?.detail || e?.message))
    }

    // 拉取历史日志
    try {
      const lg: any = await trainingApi.getLog(row.id)
      const historical: string[] = Array.isArray(lg?.log) ? lg.log : []
      if (historical.length > 0) {
        log.value = [
          `[${new Date().toLocaleTimeString()}] 已加载历史日志 (${historical.length} 行)`,
          ...historical,
        ]
      }
    } catch { /* 拉取失败不影响主流程 */ }

    await nextTick()
    initChart()

    // 仅当任务活跃时连 SSE
    if (row.celery_task_id && (row.state === 'PENDING' || row.state === 'PROGRESS')) {
      startStream(row.celery_task_id)
      historyTimer = setInterval(() => refreshHistory(row.celery_task_id), 5000)
    } else if (row.celery_task_id) {
      // 终态也拉一次历史曲线
      refreshHistory(row.celery_task_id)
    }
  }

  // ============== 关闭清理 ==============
  const cleanup = () => {
    if (cancelStream) { cancelStream(); cancelStream = null }
    if (historyTimer) { clearInterval(historyTimer); historyTimer = null }
    chart?.dispose()
    chart = null
  }

  /** 供 template 监听 history 变化重绘图表 */
  const onHistoryChanged = () => updateChartFromHistory()

  return {
    // state
    visible, job, error, progress, state,
    currentEpoch, totalEpochs, message, log, history, chartEl,
    // computed (display fallback)
    displayDataTotal, displayDataTrain, displayDataVal, displayNumClasses, displayClassNames,
    // 增量训练
    pretrainedLoaded, pretrainedPath, pretrainedError,
    // actions
    openDetail, cleanup, onHistoryChanged,
  }
}
