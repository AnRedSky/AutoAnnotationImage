/**
 * API 客户端统一入口（与后端 FastAPI 路由 1:1 对齐）
 */
import http from './http'

// <img> 标签无法附加 Authorization header，但后端 /api/files 已支持可选鉴权。
// 仍然拼 token query 主要是为了：
//   1) 记录当前访问用户到访问日志
//   2) 后端未来开启严格权限时无需改前端
// token 不存在时返回空字符串（URL 仍能工作）
function authQuery(extra: string = ''): string {
  const t = localStorage.getItem('token')
  if (!t) return extra
  return extra ? `${extra}&token=${encodeURIComponent(t)}` : `token=${encodeURIComponent(t)}`
}

// ============== 认证 ==============
export const authApi = {
  login: (username: string, password: string) => {
    const form = new FormData()
    form.append('username', username)
    form.append('password', password)
    return http.post('/auth/login', form, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })
  },
  register: (data: { username: string; password: string; email?: string; role?: string }) =>
    http.post('/auth/register', data),
  me: () => http.get('/auth/me'),
  logout: () => http.post('/auth/logout')
}

// ============== 数据集 ==============
export const datasetApi = {
  list: (params?: any) => http.get('/datasets', { params }),
  create: (data: {
    name: string
    description?: string
    task_type?: string
    category_names?: string[]
  }) =>
    http.post('/datasets', data),
  get: (id: number) => http.get(`/datasets/${id}`),
  remove: (id: number) => http.delete(`/datasets/${id}`),
  categories: (id: number) => http.get(`/datasets/${id}/categories`),
  createCategory: (id: number, data: { name: string; description?: string }) =>
    http.post(`/datasets/${id}/categories`, data)
}

// ============== 图像 ==============
export interface ImageListParams {
  status?: string
  page?: number
  page_size?: number
}

export const imageApi = {
  upload: (
    datasetId: number,
    formData: FormData,
    onUploadProgress?: (e: { loaded: number; total?: number }) => void
  ) =>
    http.post(`/images/upload/${datasetId}`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (e) => {
        if (onUploadProgress && e.total) onUploadProgress({ loaded: e.loaded, total: e.total })
      }
    }),
  autoLabel: (
    datasetId: number,
    params: {
      model_name?: string
      model_id?: number
      confidence_threshold: number
      use_finetune: boolean
    }
  ) =>
    http.post(`/images/auto-label/${datasetId}`, null, {
      params: {
        model_name: params.model_name ?? 'efficientnet_b0',
        model_id: params.model_id,
        confidence_threshold: params.confidence_threshold,
        use_finetune: params.use_finetune,
      },
    }),
  list: (datasetId: number, params?: ImageListParams & { exclude_id?: number; exclude_ids?: string }) =>
    http.get(`/images/list/${datasetId}`, { params }),
  detail: (id: number) => http.get(`/images/${id}`),
  remove: (id: number) => http.delete(`/images/${id}`),
  batchRemove: (ids: number[]) => http.post('/images/batch-delete', ids),
  fileUrl: (id: number) => `${http.defaults.baseURL}/files/${id}`,
  thumbnailUrl: (id: number, size = 240) =>
    `${http.defaults.baseURL}/files/${id}/thumbnail?size=${size}`,
  /**
   * 非破坏性测评: 对指定图片跑模型, 返回 top-1 置信度及在当前阈值下是否会被自动标注
   * - 不修改任何图片状态
   * - 不写审计日志
   * - 用户可在「启动 AI 预标注」前先看到哪些图会被标、哪些会留在待标注
   * - 后端: POST /api/images/preview-confidence
   */
  previewConfidence: (
    datasetId: number,
    imageIds: number[],
    params: {
      model_name?: string
      model_id?: number | null
      confidence_threshold: number
      use_finetune: boolean
    }
  ) =>
    http.post(`/images/preview-confidence`, {
      dataset_id: datasetId,
      image_ids: imageIds,
      model_name: params.model_name ?? 'efficientnet_b0',
      model_id: params.model_id ?? null,
      confidence_threshold: params.confidence_threshold,
      use_finetune: params.use_finetune,
    }),
}

// ============== 标注 ==============
export const annotationApi = {
  save: (data: {
    image_id: number
    label_id: number
    time_spent_ms: number
    is_confirm: boolean
  }) => http.post('/annotations/save', data),
  stats: (datasetId: number) => http.get(`/annotations/stats/${datasetId}`),
  list: (datasetId: number, params?: { page?: number; page_size?: number; action?: string }) =>
    http.get(`/annotations/list/${datasetId}`, { params }),
  recent: (limit = 20) => http.get('/annotations/recent', { params: { limit } }),
  /**
   * 批量去除图片的人工标注 (不清空 AI 预标注)
   * @param imageIds 图片 id 列表 (单/多张都行)
   * @returns { cleared, skipped, missing, items: [{image_id, filename, result, ...}] }
   */
  clear: (imageIds: number[]) => http.post('/annotations/clear', { image_ids: imageIds })
}

// ============== AI 自动标注 ==============
export const autoAnnotateApi = {
  run: (data: {
    dataset_id: number
    model_name: string
    confidence_threshold: number
    async_mode?: boolean
  }) => http.post('/auto-annotate/run', data),
  status: (taskId: string) => http.get(`/auto-annotate/status/${taskId}`),
  models: () => http.get('/auto-annotate/models'),
  // 新增: 走 /api/images/auto-label/{dataset_id} 接口, 支持切换 fine-tune/ImageNet
  // 返回: { total, auto_labeled, need_human, avg_confidence, threshold, used_finetune, model_name, model_path }
  autoLabel: (datasetId: number, params: {
    model_name?: string
    model_id?: number        // 用户在前端选择的具体微调模型 id (训练任务产出)
    confidence_threshold: number
    use_finetune?: boolean
  }) => http.post(`/images/auto-label/${datasetId}`, null, {
    params: {
      model_name: params.model_name ?? 'efficientnet_b0',
      model_id: params.model_id,
      confidence_threshold: params.confidence_threshold,
      use_finetune: params.use_finetune ?? false,
    },
  })
}

// ============== 训练 ==============
// 列表分页 (total + items + page + page_size) 走 GET /training/jobs?page=&page_size=
// 详情 / 行操作 (启动/暂停/取消/删除) 走 /jobs/{id}/* 多个端点
// 实时进度走 streamProgress (SSE)
export const trainingApi = {
  start: (data: {
    dataset_id: number
    base_model: string
    model_name: string
    epochs?: number
    batch_size?: number
    learning_rate?: number
  }) =>
    http.post(
      `/training/start?dataset_id=${data.dataset_id}&base_model=${data.base_model}` +
        `&model_name=${data.model_name}&epochs=${data.epochs || 20}` +
        `&batch_size=${data.batch_size || 32}&learning_rate=${data.learning_rate || 0.0001}`
    ),
  // 旧 REST 轮询 (保留兼容, 推荐改用 streamProgress)
  progress: (taskId: string) => http.get(`/training/progress/${taskId}`),
  // 训练历史曲线 (从 Redis 拉, 每个 epoch 结束 worker 会写)
  history: (taskId: string) => http.get(`/training/history/${taskId}`),

  // 列表分页: params = { page, page_size, dataset_id?, state?, q? }
  // q: 关键词模糊搜索, 同时匹配 model_name 与 base_model (大小写不敏感)
  // 后端返回 { total, items, page, page_size }
  jobs: (params: { page?: number; page_size?: number; dataset_id?: number; state?: string; q?: string } = {}) =>
    http.get('/training/jobs/', { params }),
  // 单条详情
  job: (jobId: number) => http.get(`/training/jobs/${jobId}`),
  // 行操作 — 复用旧配置重新提交
  // - mode: restart=再训练(默认) | resume=继续(复用同 job)
  // - mode=restart: 后端预创建新 TrainingJob 行 (state=PENDING),
  //   返回 { new_job_id, task_id, ... }, 前端可以据此塞占位行
  // - mode=resume: 复用旧行, 仅更新 state/celery_task_id
  // - params: 仅 mode=restart 生效, 后端会把 params 合并到旧 job 字段上,
  //   新 model_name 自动加 _r{timestamp} 后缀
  startJob: (
    jobId: number,
    mode: 'restart' | 'resume' = 'restart',
    params?: {
      dataset_id?: number
      base_model?: string
      model_name?: string
      epochs?: number
      batch_size?: number
      learning_rate?: number
    }
  ) =>
    http.post<{
      success: boolean
      job_id: number
      new_job_id?: number
      state?: string
      message?: string
      task_id?: string
    }>(
      `/training/jobs/${jobId}/start${mode === 'resume' ? '?mode=resume' : ''}`,
      params && Object.keys(params).length > 0 ? params : undefined
    ),
  // 行操作 — 暂停 (worker 在 epoch 边界写 PAUSED)
  pauseJob: (jobId: number) => http.post(`/training/jobs/${jobId}/pause`),
  // 行操作 — 取消 (硬停止, REVOKED)
  cancel: (jobId: number) => http.post(`/training/jobs/${jobId}/cancel`),
  // 行操作 — 删除 (仅终态允许)
  removeJob: (jobId: number) => http.delete(`/training/jobs/${jobId}`),
  // 错误详情
  error: (jobId: number) => http.post(`/training/jobs/${jobId}/error`),
  // 训练日志 (D3 持久化, 详情页打开可还原历史日志)
  getLog: (jobId: number) => http.get<{ log: string[] }>(`/training/jobs/${jobId}/log`),
  appendLog: (jobId: number, line: string) =>
    http.post<{ log: string[] }>(`/training/jobs/${jobId}/log`, { line }),
  // 编辑训练任务参数 (仅未运行任务允许)
  updateJob: (jobId: number, data: {
    dataset_id?: number
    base_model?: string
    model_name?: string
    epochs?: number
    batch_size?: number
    learning_rate?: number
  }) => http.patch(`/training/jobs/${jobId}`, data),

  /**
   * SSE 实时进度订阅
   * @param taskId Celery 任务 ID
   * @param callbacks.onMessage - 每帧 data: 事件回调 (payload: TrainStatusResponse)
   * @param callbacks.onComplete - 服务端推完 end 事件后回调 (训练已结束)
   * @param callbacks.onError - 连接/解析异常回调
   * @returns cancel() 函数, 调用后立即关闭 fetch + abort
   *
   * 为什么不用 EventSource:
   *   EventSource 不支持自定义 header, 鉴权只能走 query. 但本接口后端
   *   已经是可选鉴权, 仍保留 query token 兼容; 这里选 fetch + ReadableStream
   *   是为了拿到 AbortController 的精确控制 (切页/刷新能立刻断开, 不留僵尸连接).
   */
  streamProgress: (
    taskId: string,
    callbacks: {
      onMessage: (data: any) => void
      onComplete?: () => void
      onError?: (err: Error) => void
    }
  ): (() => void) => {
    const baseURL = (http.defaults.baseURL as string) || ''
    const token = localStorage.getItem('token') || ''
    const qs = token ? `?token=${encodeURIComponent(token)}` : ''
    const url = `${baseURL}/training/progress/stream/${taskId}${qs}`

    const controller = new AbortController()
    let finished = false

    const finish = (code: 'complete' | 'error', err?: Error) => {
      if (finished) return
      finished = true
      if (code === 'complete') callbacks.onComplete?.()
      else if (err) callbacks.onError?.(err)
      try { controller.abort() } catch {}
    }

    ;(async () => {
      let res: Response
      try {
        res = await fetch(url, {
          method: 'GET',
          signal: controller.signal,
          headers: {
            Accept: 'text/event-stream',
            'Cache-Control': 'no-cache',
          },
        })
      } catch (e: any) {
        if (e?.name !== 'AbortError') finish('error', e instanceof Error ? e : new Error(String(e)))
        return
      }

      if (!res.ok || !res.body) {
        finish('error', new Error(`SSE 连接失败: HTTP ${res.status}`))
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''

      try {
        while (true) {
          const { value, done } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })

          // SSE 帧以 \n\n 结束, 用换行切分后保留最后一段 (可能不完整)
          let idx: number
          // eslint-disable-next-line no-cond-assign
          while ((idx = buffer.indexOf('\n\n')) !== -1) {
            const rawFrame = buffer.slice(0, idx)
            buffer = buffer.slice(idx + 2)
            if (!rawFrame) continue

            // 注释帧 (`: keepalive ...`) 直接跳过
            if (rawFrame.startsWith(':')) continue

            // 解析 event: / data: 多行字段
            let eventName = 'message'
            const dataLines: string[] = []
            for (const line of rawFrame.split('\n')) {
              if (line.startsWith(':')) continue
              if (line.startsWith('event:')) {
                eventName = line.slice(6).trim()
              } else if (line.startsWith('data:')) {
                dataLines.push(line.slice(5).trimStart())
              }
            }
            if (!dataLines.length) continue
            const dataStr = dataLines.join('\n')
            let payload: any
            try { payload = JSON.parse(dataStr) } catch { continue }

            callbacks.onMessage(payload)

            // 终端态由事件或 payload.state 双重判定
            if (
              eventName === 'end' ||
              payload?.state === 'SUCCESS' ||
              payload?.state === 'FAILURE' ||
              payload?.state === 'REVOKED'
            ) {
              finish('complete')
              return
            }
          }
        }
        // 服务端正常关闭流 → 视为完成
        finish('complete')
      } catch (e: any) {
        if (e?.name !== 'AbortError') {
          finish('error', e instanceof Error ? e : new Error(String(e)))
        }
      }
    })()

    return () => {
      if (!finished) {
        finished = true
        try { controller.abort() } catch {}
      }
    }
  },
}

// ============== 模型版本 ==============
export const modelApi = {
  // 修复: 带尾斜杠避免 FastAPI 404 (后端 @router.get("/") 必须 /models/ 才命中)
  // 列表支持按 dataset_id / active 过滤, 用于「只显示该 dataset 激活的模型」
  list: (params?: { dataset_id?: number; active?: boolean }) =>
    http.get('/models/', { params: params || {} }),
  // 当前激活模型列表 (供标注工作台 / 概览面板显示)
  // v2 改造: 返回 list, 支持多激活并存; datasetId 必传 (按数据集过滤)
  // 后端默认: { items: [...] }, 兼容旧前端 { model: ...}
  getActive: (datasetId?: number) =>
    http.get('/models/active', { params: datasetId ? { dataset_id: datasetId } : {} }),
  detail: (id: number) => http.get(`/models/${id}/detail`),
  // v2 语义: 不再自动取消同 dataset 其他激活 (允许多激活并存)
  activate: (id: number) => http.post(`/models/${id}/activate`),
  // 新增: 取消激活 (幂等)
  deactivate: (id: number) => http.post(`/models/${id}/deactivate`),
  remove: (id: number) => http.delete(`/models/${id}`),
  // v2 改造: batch-activate 接受 active 字段, true=激活, false=取消激活
  // 成功:  { success, ids, active }
  batchSetActive: (ids: number[], active: boolean) =>
    http.post('/models/batch-activate', { ids, active }),
  // 批量删除: 后端单事务, 任一不存在则 4xx 全部回滚 (激活的不再拒绝, v2 改造)
  // 成功返回 {success, deleted_ids, files_deleted, detail:[{id, name, deleted_file}]}
  batchRemove: (ids: number[]) => http.post('/models/batch-delete', { ids }),
  compare: (a: number, b: number) =>
    http.get(`/stats/models/compare/${a}/${b}`)
}

// ============== 导出 ==============
export const exportApi = {
  coco: (datasetId: number) => `${http.defaults.baseURL}/export/coco/${datasetId}?${authQuery()}`,
  yolo: (datasetId: number) => `${http.defaults.baseURL}/export/yolo/${datasetId}?${authQuery()}`,
  csv: (datasetId: number) => `${http.defaults.baseURL}/export/csv/${datasetId}?${authQuery()}`
}

// ============== 统计分析 ==============
export const statsApi = {
  overview: () => http.get('/stats/overview'),
  dataset: (datasetId: number) => http.get(`/stats/dataset/${datasetId}`),
  confidence: (datasetId: number) => http.get(`/stats/confidence/${datasetId}`),
  timeline: (datasetId: number, days = 7) =>
    http.get(`/stats/timeline/${datasetId}`, { params: { days } }),
  annotatorEfficiency: () => http.get('/stats/annotator-efficiency')
}

// ============== v2.0.0 S3+: 目标检测 ==============
/**
 * 目标检测 API 客户端 (与后端 /api/detection/* 端点对齐)
 * 包含: bbox 标注 CRUD / YOLO 训练 / 自动标注 / SSE 进度 / 训练历史
 */
export const detectionApi = {
  // ---- bbox 标注 ----
  listBBoxes: (imageId: number) =>
    http.get(`/detection/annotations/${imageId}`),
  saveBBox: (imageId: number, data: {
    x_min: number; y_min: number; x_max: number; y_max: number;
    category_id: number; confidence?: number; source?: string;
  }) => http.post('/detection/annotations/save', data, {
    params: { image_id: imageId },
  }),
  removeBBox: (id: number) => http.delete(`/detection/annotations/${id}`),
  clearBBoxes: (imageId: number) =>
    http.delete(`/detection/annotations/clear/${imageId}`),
  // ---- 训练 ----
  startTrain: (data: {
    dataset_id: number
    model_name?: string         // yolov8n/s/m/l/x
    model_alias?: string        // 落盘 ModelVersion.name
    epochs?: number
    imgsz?: number
    batch?: number
    val_ratio?: number
    device?: string
  }) => http.post('/detection/train', data),
  // ---- 自动标注 ----
  startAutoAnnotate: (data: {
    dataset_id: number
    model_version_id: number
    conf_threshold?: number
    iou_threshold?: number
    imgsz?: number
    device?: string
    overwrite_existing?: boolean
  }) => http.post('/detection/auto-annotate', data),
  // ---- 进度 (旧: 轮询; 新: SSE) ----
  progress: (taskId: string) => http.get(`/detection/progress/${taskId}`),
  history: (taskId: string) => http.get(`/detection/history/${taskId}`),
  // ---- 模型版本管理 ----
  listModels: (params?: { dataset_id?: number; task_type?: string }) =>
    http.get('/detection/models/', { params: params || {} }),
  activateModel: (id: number) => http.post(`/detection/models/${id}/activate`),
  // ---- SSE 实时进度 (复用 trainingApi.streamProgress 同源设计) ----
  streamProgress: (
    taskId: string,
    callbacks: {
      onMessage: (data: any) => void
      onComplete?: () => void
      onError?: (err: Error) => void
    }
  ): (() => void) => {
    const baseURL = (http.defaults.baseURL as string) || ''
    const token = localStorage.getItem('token') || ''
    const qs = token ? `?token=${encodeURIComponent(token)}` : ''
    const url = `${baseURL}/detection/progress/stream/${taskId}${qs}`

    const controller = new AbortController()
    let finished = false
    const finish = (code: 'complete' | 'error', err?: Error) => {
      if (finished) return
      finished = true
      if (code === 'complete') callbacks.onComplete?.()
      else if (err) callbacks.onError?.(err)
      try { controller.abort() } catch {}
    }
    ;(async () => {
      let res: Response
      try {
        res = await fetch(url, {
          method: 'GET', signal: controller.signal,
          headers: { Accept: 'text/event-stream', 'Cache-Control': 'no-cache' },
        })
      } catch (e: any) {
        if (e?.name !== 'AbortError') finish('error', e instanceof Error ? e : new Error(String(e)))
        return
      }
      if (!res.ok || !res.body) {
        finish('error', new Error(`SSE 连接失败: HTTP ${res.status}`))
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      try {
        while (true) {
          const { value, done } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          let idx: number
          while ((idx = buffer.indexOf('\n\n')) !== -1) {
            const rawFrame = buffer.slice(0, idx)
            buffer = buffer.slice(idx + 2)
            if (!rawFrame || rawFrame.startsWith(':')) continue
            let eventName = 'message'
            const dataLines: string[] = []
            for (const line of rawFrame.split('\n')) {
              if (line.startsWith(':')) continue
              if (line.startsWith('event:')) eventName = line.slice(6).trim()
              else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
            }
            if (!dataLines.length) continue
            let payload: any
            try { payload = JSON.parse(dataLines.join('\n')) } catch { continue }
            callbacks.onMessage(payload)
            if (eventName === 'end' || ['SUCCESS', 'FAILURE', 'REVOKED'].includes(payload?.state)) {
              finish('complete'); return
            }
          }
        }
        finish('complete')
      } catch (e: any) {
        if (e?.name !== 'AbortError') finish('error', e instanceof Error ? e : new Error(String(e)))
      }
    })()
    return () => { if (!finished) { finished = true; try { controller.abort() } catch {} } }
  },
}

// ============== v2.0.0 S5+: 图像分割 ==============
/**
 * 图像分割 API 客户端 (与后端 /api/segmentation/* 端点对齐)
 * 包含: mask CRUD / DeepLabV3+ 训练 / 自动标注 / 进度查询
 */
export const segmentationApi = {
  // ---- mask CRUD ----
  uploadMask: (imageId: number, file: File, source = 'human') => {
    const form = new FormData()
    form.append('file', file)
    return http.post(`/segmentation/masks/upload/${imageId}`, form, {
      params: { source },
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  getMask: (imageId: number, download = false) =>
    http.get(`/segmentation/masks/${imageId}`, { params: download ? { download: 'true' } : {} }),
  removeMask: (maskId: number) => http.delete(`/segmentation/masks/${maskId}`),
  listMasks: (imageIds: number[]) =>
    http.get('/segmentation/masks/list', { params: { image_ids: imageIds.join(',') } }),
  // ---- 训练 ----
  startTrain: (data: {
    dataset_id: number
    backbone?: string            // deeplabv3_resnet50 / 101
    model_alias?: string
    epochs?: number
    batch_size?: number
    crop_size?: number
    learning_rate?: number
    device?: string
    val_ratio?: number
  }) => http.post('/segmentation/train', data),
  // ---- 自动标注 ----
  startAutoAnnotate: (data: {
    dataset_id: number
    model_version_id: number
    device?: string
    overwrite_existing?: boolean
  }) => http.post('/segmentation/auto-annotate', data),
  // ---- 进度 ----
  progress: (taskId: string) => http.get(`/segmentation/progress/${taskId}`),
  history: (taskId: string) => http.get(`/segmentation/history/${taskId}`),
  // ---- 模型版本管理 ----
  listModels: (params?: { dataset_id?: number; task_type?: string }) =>
    http.get('/segmentation/models/', { params: params || {} }),
  activateModel: (id: number) => http.post(`/segmentation/models/${id}/activate`),
}

// ============== v2.0.0 S6+: 导出扩展 ==============
/**
 * 检测/分割专用导出 URL 构造
 * - 返回可直接用于 <a href> / window.open 的完整 URL
 * - token 走 query (与 exportApi.coco / .yolo 风格保持一致)
 */
export const exportApiV2 = {
  yoloDet: (datasetId: number, valRatio = 0.2) =>
    `${http.defaults.baseURL}/export/yolo-det/${datasetId}?val_ratio=${valRatio}&${authQuery()}`,
  cocoDet: (datasetId: number, includePending = false) =>
    `${http.defaults.baseURL}/export/coco-det/${datasetId}?include_pending=${includePending}&${authQuery()}`,
  vocSeg: (datasetId: number, valRatio = 0.2, includePending = false) =>
    `${http.defaults.baseURL}/export/voc-seg/${datasetId}?val_ratio=${valRatio}` +
    `&include_pending=${includePending}&${authQuery()}`,
  cocoSeg: (datasetId: number, includePending = false) =>
    `${http.defaults.baseURL}/export/coco-seg/${datasetId}?include_pending=${includePending}&${authQuery()}`,
}
