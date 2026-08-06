/**
 * useTrainingDetailStream - 训练详情 SSE 流 (走共享池) + 数据集统计 + 历史曲线
 *
 * v3.1.0 Phase T1 (性能优化): 改用 useTrainingSsePool 共享底层 SSE 连接
 * - 旧实现详情打开时单独调 trainingApi.streamProgress, 与列表订阅并发, 同一 taskId 两条流
 * - 新实现走共享池, 详情关闭后池延迟 500ms~1.5s 才真关连接, 让快进快出场景复用
 *
 * v3.1.0 Phase T2 (性能优化): history 轮询改为 SSE 驱动
 * - 旧实现固定 setInterval(refreshHistory, 5000), 5 秒轮询历史曲线
 * - 新实现: SSE 帧 current_epoch 变更时立即刷一次; 若 8 秒内无 epoch 变更, 再退化
 *   到 8 秒轮询兜底 (worker 偶发不推 SSE 帧), 全程 history 拉取最坏一次 / 8s
 *
 * 数据集统计双源 fallback (SSE 实时 ref + DB 持久化):
 * - displayDataTotal / displayDataTrain / displayDataVal / displayNumClasses / displayClassNames
 *   computed 优先取 SSE 推的 ref, null 时回退到 detailJob 的 DB 字段
 */
import { ref, computed, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import * as echarts from 'echarts'
import { trainingApi } from '@/api'
import { useTrainingSsePool } from './useTrainingSsePool'

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

// 历史曲线刷新节流常量
const HISTORY_MIN_INTERVAL_MS = 1500   // 同一 taskId 至少 1.5 秒才允许刷新一次 (防抖)
const HISTORY_IDLE_BACKOFF_MS = 8000   // 无 epoch 变化时的兜底轮询间隔
const HISTORY_LIVE_CHECK_MS = 2000     // epoch 变更后多久不再主动拉 (SSE 接管即停止)

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

  // ---- 内部: ECharts / SSE 取消 / timer 句柄 ----
  let chart: echarts.ECharts | null = null
  let sseOff: (() => void) | null = null
  let historyBackoffTimer: ReturnType<typeof setTimeout> | null = null
  let lastLogSaveAt = 0
  let lastHistoryFetchAt = 0
  let lastEpochSeen: number | null = null
  let historyLiveUntilAt = 0   // 在此时间之前由 SSE 驱动, 不退化轮询

  // v3.6.5 HOTFIX: SSE 帧 → log.value 实时追加 (修复 "训练日志没同步 SSE 更新")
  // - 背景: 之前 onStreamFrame 只更新 state/progress/message.value 等 ref,
  //         从未向 log.value 追加新行 → 详情页日志面板只能看到打开时拉到的历史
  // - 修复: 在 onStreamFrame 中基于 (state, current_epoch) 签名去重,
  //         新帧签名不同则追加到 log.value, 并调 saveDetailLog 持久化到 DB
  // - 边界: 与 _LAST_LOG_SIG (后端去重) 互不冲突, 双层防护
  let lastLoggedState: string | null = null
  let lastLoggedEpoch: number | null = null

  const pool = useTrainingSsePool()

  // ============== 持久化一行训练日志到后端 ==============
  const saveDetailLog = (taskId: string, line: string) => {
    const jobId = job.value?.id
    if (!jobId) return
    const now = Date.now()
    if (now - lastLogSaveAt < 500) return
    lastLogSaveAt = now
    trainingApi.appendLog(jobId, line).catch(() => { /* best-effort */ })
  }

  // v3.6.5 HOTFIX: 把 SSE 帧构造成与后端 _build_log_line 格式一致的日志行
  // 后端格式: [YYYY-MM-DD HH:MM:SS] state=PROGRESS progress=42.5% epoch=8/20 msg=...
  // 同步时间戳格式便于前后端日志视觉对齐 (前端时间戳比后端晚几毫秒, 不影响签名去重)
  const buildLogLineFromSseFrame = (data: any): string => {
    const ts = new Date().toISOString().replace('T', ' ').substring(0, 19)
    const state = data.state || 'PROGRESS'
    const progress =
      typeof data.progress === 'number' ? `${data.progress.toFixed(1)}%` : ''
    const epoch = data.current_epoch
    const total = data.total_epochs
    const msg = data.message || ''
    const parts: string[] = [`[${ts}]`, `state=${state}`]
    if (progress) parts.push(`progress=${progress}`)
    if (epoch != null || total != null) {
      parts.push(`epoch=${epoch != null ? epoch : '-'}/${total != null ? total : '-'}`)
    }
    if (msg) parts.push(`msg=${msg}`)
    return parts.join(' ')
  }

  // v3.6.5 HOTFIX: SSE 帧 → 追加到 log.value + 调 saveDetailLog 持久化
  // - 签名 = (state, current_epoch) — 二者任一变化即视为新行
  // - 设计要点: 不基于 message 去重, 因为 classification 的 progress_cb 每个
  //   batch 都推不同 message, 20 epoch × 200 batch = 4000 行, 用户无法阅读
  //   - 每个 epoch 内的 per-batch 帧: current_epoch=undefined, 全部去重
  //   - epoch_callback 帧: current_epoch=N, 与上一帧 epoch 不等 → 追加 1 行
  //   - 新 epoch 首个 per-batch 帧: current_epoch=undefined, 与上一帧 (N) 不等
  //     → 追加 1 行 "Epoch N+1 batch 1/Y" (作为新 epoch 起始标记, 信息无害)
  //   - 终态帧: state 变化 → 追加 1 行
  // - 与后端 _LAST_LOG_SIG (state, progress, msg) 兼容, epoch 维度是额外保护
  // - 与 TrainingJob.log 持久化互不冲突, saveDetailLog 后端会自行去重
  const appendLogFromSseFrame = (data: any) => {
    if (!data || typeof data !== 'object') return
    const newState = data.state || 'PROGRESS'
    const newEpoch = data.current_epoch ?? null
    const isNew = (
      newState !== lastLoggedState ||
      newEpoch !== lastLoggedEpoch
    )
    if (!isNew) return
    lastLoggedState = newState
    lastLoggedEpoch = newEpoch

    const line = buildLogLineFromSseFrame(data)
    // 内存追加: 详情页日志面板即时刷新
    log.value.push(line)
    // 持久化: 通过 saveDetailLog 写到后端 TrainingJob.log (best-effort, 500ms 节流)
    if (job.value?.celery_task_id) {
      saveDetailLog(job.value.celery_task_id, line)
    }
  }

  // ============== 历史曲线刷新 (节流 + 智能触发) ==============
  const refreshHistory = async (taskId: string, opts: { force?: boolean } = {}) => {
    const now = Date.now()
    if (!opts.force && now - lastHistoryFetchAt < HISTORY_MIN_INTERVAL_MS) return
    lastHistoryFetchAt = now
    try {
      const h: any = await trainingApi.history(taskId)
      history.value = h?.history || []
    } catch { /* 拉取失败不影响主流程 */ }
  }

  // SSE 帧到时: 立即刷新 history (epoch 变更场景), 设置 liveUntil 让兜底轮询让位
  const onStreamFrame = (data: any) => {
    const newState = data.state || 'PROGRESS'
    state.value = newState
    if (job.value) job.value.state = newState
    progress.value = Number(data.progress || 0)
    currentEpoch.value = data.current_epoch ?? null
    totalEpochs.value = data.total_epochs ?? totalEpochs.value
    message.value = data.message || message.value

    // v3.6.5 HOTFIX: SSE 帧到时同步追加到 log.value (修复日志面板不更新问题)
    // - 必须放在 message.value 更新之后 (用新值判重), 但 log.value 之前
    // - 内部用 lastLogged* 三个局部变量做签名, 不会受 state/progress 同步赋值影响
    appendLogFromSseFrame(data)

    if (data.started_at && job.value) {
      const tsNew = new Date(data.started_at).getTime()
      const tsOld = job.value.started_at ? new Date(job.value.started_at).getTime() : 0
      if (tsNew && Math.abs(tsNew - tsOld) > 1000) job.value.started_at = String(data.started_at)
    }
    if (data.finished_at && job.value) {
      const tsNew = new Date(data.finished_at).getTime()
      const tsOld = job.value.finished_at ? new Date(job.value.finished_at).getTime() : 0
      if (tsNew && Math.abs(tsNew - tsOld) > 1000) job.value.finished_at = String(data.finished_at)
    }

    // 数据集统计 / 增量训练状态
    if (typeof data.data_total === 'number') dataTotal.value = data.data_total
    if (typeof data.data_train === 'number') dataTrain.value = data.data_train
    if (typeof data.data_val === 'number') dataVal.value = data.data_val
    if (typeof data.num_classes === 'number') numClasses.value = data.num_classes
    if (Array.isArray(data.class_names)) classNames.value = data.class_names
    if (typeof data.pretrained_loaded === 'boolean') pretrainedLoaded.value = data.pretrained_loaded
    if (typeof data.pretrained_path === 'string') pretrainedPath.value = data.pretrained_path
    if (typeof data.pretrained_error === 'string') pretrainedError.value = data.pretrained_error

    // 终态: 重新拉完整 job 拿 finished_at / duration / device_info
    if (['SUCCESS', 'FAILURE', 'REVOKED'].includes(newState) && job.value?.id) {
      trainingApi.job(job.value.id).then((d: any) => { job.value = d })
        .catch(() => { /* 拉取失败不影响其他字段 */ })
    }

    // History: 仅在 epoch 变化时立即拉 (SSE 帧自带 current_epoch)
    const frameEpoch = data.current_epoch
    if (typeof frameEpoch === 'number' && frameEpoch !== lastEpochSeen) {
      const isEpochAdvance = lastEpochSeen == null || frameEpoch > lastEpochSeen
      lastEpochSeen = frameEpoch
      if (isEpochAdvance && job.value?.celery_task_id) {
        historyLiveUntilAt = Date.now() + HISTORY_LIVE_CHECK_MS
        refreshHistory(job.value.celery_task_id)
      }
    }
  }

  // 退化兜底轮询 (worker 偶发不推 SSE 帧): 仅当 SSE 长时间空闲时启动
  const armHistoryBackoff = (taskId: string) => {
    if (historyBackoffTimer) {
      clearTimeout(historyBackoffTimer)
      historyBackoffTimer = null
    }
    const schedule = () => {
      historyBackoffTimer = setTimeout(async () => {
        const now = Date.now()
        // SSE 接管中 → 跳过本次, 重新排程
        if (now < historyLiveUntilAt) {
          schedule()
          return
        }
        if (visible.value && job.value?.celery_task_id === taskId) {
          await refreshHistory(taskId, { force: true })
        }
        schedule()
      }, HISTORY_IDLE_BACKOFF_MS)
    }
    schedule()
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

  // 用 optional chain 避免字段缺失时崩 (旧 detection/segmentation rows 可能没 train_loss)
  const safeFixed = (v: any, digits = 4) => {
    const n = Number(v)
    return Number.isFinite(n) ? +n.toFixed(digits) : 0
  }

  const updateChartFromHistory = () => {
    if (!chart || history.value.length === 0) return
    const h = history.value
    const isDetection = job.value?.task_type === 'detection'
    const isSegmentation = job.value?.task_type === 'segmentation'
    if (isDetection) {
      chart.setOption({
        xAxis: { data: h.map((x: any) => x.epoch) },
        series: [
          { data: h.map((x: any) => safeFixed(x.box_loss)) },
          { data: h.map((x: any) => safeFixed(x.cls_loss)) },
          { data: h.map((x: any) => safeFixed(x.map_50)) },
          { data: h.map((x: any) => safeFixed(x.map_50_95)) },
          { data: h.map((x: any) => safeFixed(x.precision)) },
          { data: h.map((x: any) => safeFixed(x.recall)) },
        ],
      })
    } else if (isSegmentation) {
      chart.setOption({
        xAxis: { data: h.map((x: any) => x.epoch) },
        series: [
          { data: h.map((x: any) => safeFixed(x.train_loss)) },
          { data: h.map((x: any) => safeFixed(x.val_loss)) },
          { data: h.map((x: any) => safeFixed(x.miou)) },
          { data: h.map((x: any) => safeFixed(x.pixel_acc)) },
          { data: h.map((x: any) => safeFixed(x.dice)) },
        ],
      })
    } else {
      chart.setOption({
        xAxis: { data: h.map((x: any) => x.epoch) },
        series: [
          { data: h.map((x: any) => safeFixed(x.train_loss)) },
          { data: h.map((x: any) => safeFixed(x.val_loss)) },
          { data: h.map((x: any) => safeFixed(x.train_acc)) },
          { data: h.map((x: any) => safeFixed(x.val_acc)) },
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
    lastEpochSeen = null
    historyLiveUntilAt = 0
    lastHistoryFetchAt = 0
    // v3.6.5 HOTFIX: 重置 lastLogged* 签名, 防止上一任务的 SSE 状态污染新详情
    lastLoggedState = null
    lastLoggedEpoch = null

    try {
      const d: any = await trainingApi.job(row.id)
      job.value = d
      progress.value = Number(d.progress || 0)
      state.value = d.state
      totalEpochs.value = d.epochs
      message.value = d.message || ''
      // v3.6.5 HOTFIX: 初始化 SSE 帧签名为 DB 当前状态
      // - 第一个 SSE 帧若与 DB 状态一致 (worker 还没推新帧) → 签名匹配 → 跳过
      // - 第一个 SSE 帧若 worker 已推新帧 (state/epoch 变化) → 追加一行
      // - 避免重复: 历史 log 已包含 worker 在 DB 快照时已写入的最后一行
      lastLoggedState = d.state || null
      lastLoggedEpoch = (d.current_epoch ?? null)
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

    try {
      const lg: any = await trainingApi.getLog(row.id)
      const historical: string[] = Array.isArray(lg?.log) ? lg.log : []
      if (historical.length > 0) {
        log.value = [
          `[已加载历史日志] (${historical.length} 行)`,
          ...historical,
        ]
      }
    } catch { /* 拉取失败不影响主流程 */ }

    await nextTick()
    initChart()

    if (row.celery_task_id && (row.state === 'PENDING' || row.state === 'PROGRESS')) {
      const taskId = row.celery_task_id
      log.value.push(`[已连接 SSE 进度推送] task_id=${taskId.slice(0, 8)}…`)
      sseOff = pool.subscribe(taskId, {
        onMessage: onStreamFrame,
        onError: (err) => {
          // v3.1.0 Phase T3: 错误兜底由退化轮询接管, 不再硬切 history 拉取
          log.value.push(`[SSE 断开] ${err?.message || ''}`)
        },
      })
      armHistoryBackoff(taskId)
    } else if (row.celery_task_id) {
      // 终态: 拉一次历史即可
      refreshHistory(row.celery_task_id, { force: true })
    }
  }

  // ============== 关闭清理 ==============
  const cleanup = () => {
    if (sseOff) { sseOff(); sseOff = null }
    if (historyBackoffTimer) { clearTimeout(historyBackoffTimer); historyBackoffTimer = null }
    chart?.dispose()
    chart = null
  }

  const onHistoryChanged = () => updateChartFromHistory()

  return {
    visible, job, error, progress, state,
    currentEpoch, totalEpochs, message, log, history, chartEl,
    displayDataTotal, displayDataTrain, displayDataVal, displayNumClasses, displayClassNames,
    pretrainedLoaded, pretrainedPath, pretrainedError,
    openDetail, cleanup, onHistoryChanged,
  }
}
