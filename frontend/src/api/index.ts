/**
 * API 客户端统一入口（与后端 FastAPI 路由 1:1 对齐）
 */
import http from './http'
import { createSSEStream } from '@/utils/sse'

// <img> 标签无法附加 Authorization header，但后端 /api/files 已支持 query token。
// 仍然拼 token query 主要是为了：
//   1) 后端强制鉴权后, <img> 仍能加载 (EventSource/图片均无法设 header)
//   2) 后端可基于 token 记录访问用户, 未来做权限审计
//
// v3.0.0 全面审查修复: 改用 withToken(url) — 自动检测 URL 是否已有 query 参数,
// 选择 '?' 或 '&' 追加 token. 旧 authQuery(extra) 设计需要调用方拼前缀,
// 容易写出 "?size=320?token=xxx" 双 ? 的 422 错误.
//
// 行为对比:
//   旧: thumbnailUrl = `${BASE}/files/1/thumbnail?size=320${authQuery()}`
//       → http://.../thumbnail?size=320?token=xxx ❌ FastAPI 422
//   新: thumbnailUrl = withToken(`${BASE}/files/1/thumbnail?size=320`)
//       → http://.../thumbnail?size=320&token=xxx ✅
//
// 无 token 时: withToken 直接返回原 URL, 不会污染 (? 不再孤立)
function withToken(url: string): string {
  const t = localStorage.getItem('token')
  if (!t) return url
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}token=${encodeURIComponent(t)}`
}

// ============== 认证 ==============
export const authApi = {
  /**
   * 登录接口
   * - 使用 application/x-www-form-urlencoded (OAuth2PasswordRequestForm 规范要求)
   * - 显式设置 Content-Type 避免 axios 在 FormData 场景下自动追加 boundary 导致兼容问题
   * - 历史 bug: 曾用 FormData + 'multipart/form-data' (无 boundary) 在部分环境下触发首次
   *   请求后端无法解析表单, 表现"首次无响应/需点击两次". 已统一为 URLSearchParams.
   */
  login: (username: string, password: string) => {
    const body = new URLSearchParams()
    body.append('username', username)
    body.append('password', password)
    return http.post('/auth/login', body, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
    })
  },
  register: (data: { username: string; password: string; email?: string; role?: string }) =>
    http.post('/auth/register', data),
  /**
   * 获取当前登录用户信息
   * - 默认请求: 401 时由响应拦截器统一清 auth + 跳登录页
   * - 登录流程内的 /me 必须带 __skip401Redirect: true
   *   原因: 刚签发的新 token 写入 localStorage 与本请求发出之间存在竞态,
   *        万一请求先于 setAuth 发出 (理论上不会, 但作为纵深防御),
   *        响应拦截器不应清空刚刚写入的 token.
   */
  me: () => http.get('/auth/me', { __skip401Redirect: true } as any),
  logout: () => http.post('/auth/logout')
}

// ============== 数据集 ==============
export interface DatasetItem {
  id: number
  name: string
  description?: string | null
  task_type: string
  image_count: number
  annotated_count: number
  category_count: number
  status: string
  owner_id: number
  created_at: string | null
  // v3.3.2: 数据来源与权限字段
  source?: 'personal' | 'team_shared'
  team_id?: number | null
  team_name?: string | null
  shared_by?: string | null
  my_access?: 'owner' | 'manager' | 'editor' | 'viewer' | 'admin'
}

export const datasetApi = {
  /**
   * v3.3.2: 列表返回个人所有 + 团队共享 dataset, 含 source/team_name 等字段
   */
  list: (params?: any) => http.get<{
    items: DatasetItem[]
    total: number
    personal_count: number
    team_shared_count: number
  }>('/datasets', { params }),
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
  /**
   * v3.5.0 新增: 轻量级 id 列表接口
   * - 后端: GET /api/images/ids/{dataset_id}
   * - 仅返回 image id 数组 + total, 解决标注工作台批量操作时 100 张上限问题
   * - max_ids 默认 2000 (后端硬上限)
   * - order: 'desc' (默认, 最新优先, 与 /list 一致) / 'asc' (最旧优先)
   * - 返回字段: { items: number[], total: number, max_ids, order, truncated, status_filter }
   */
  listIds: (
    datasetId: number,
    params?: { status?: string; order?: 'asc' | 'desc'; max_ids?: number }
  ) => http.get(`/images/ids/${datasetId}`, { params }),
  detail: (id: number) => http.get(`/images/${id}`),
  remove: (id: number) => http.delete(`/images/${id}`),
  batchRemove: (ids: number[]) => http.post('/images/batch-delete', ids),
  /**
   * v3.0.0 全面审查修复 P0-2: 文件端点改为强制鉴权
   * - 旧: fileUrl 不带 token, 匿名可访问
   * - 新: fileUrl 必须拼 ?token=xxx (因为 <img> 标签无法设 header)
   * - token 缺失时降级返回无 token URL, 后端将返回 401, 前端 <img> 会 broken
   *   此时通常意味着用户未登录, 应被路由守卫拦截
   */
  fileUrl: (id: number) => withToken(`${http.defaults.baseURL}/files/${id}`),
  thumbnailUrl: (id: number, size = 240) =>
    withToken(`${http.defaults.baseURL}/files/${id}/thumbnail?size=${size}`),
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
   * 批量去除图片的标注 (v2.5.16+ 支持检测/分割)
   * - 分类: 清 final_label_id / ai_prediction / status → pending
   * - 检测: 删 BBoxAnnotation 行 (按 image_id)
   * - 分割: 删 SegmentationMask 行 + 物理 mask PNG
   * @param imageIds 图片 id 列表 (单/多张都行)
   * @returns {
   *   cleared, skipped, missing,
   *   bbox_cleared_count, mask_cleared_count,  // v2.5.16+
   *   items: [{
   *     image_id, filename, task_type, result,
   *     old_label_id, had_ai, ai_cleared,
   *     bbox_cleared, mask_cleared  // v2.5.16+ 每张图实际清理量
   *   }]
   * }
   */
  clear: (imageIds: number[]) => http.post('/annotations/clear', { image_ids: imageIds }),
  // v3.0.0: 不合格图片标记 (正交于 status 状态机, 不修改原标注)
  markUnqualified: (data: {
    image_id: number
    reason: string
    custom_text?: string
  }) => http.post('/annotations/mark-unqualified', data),
  unmarkUnqualified: (image_id: number) =>
    http.post('/annotations/unmark-unqualified', { image_id }),
  batchMarkUnqualified: (data: {
    image_ids: number[]
    reason: string
    custom_text?: string
  }) => http.post('/annotations/batch-mark-unqualified', data),
  // v3.4.0: 人工修正方案 - comment 字段 (扩展 /save 端点, 选填)
  // - 后端: payload.comment 写入 AnnotationLog.payload
  saveWithComment: (data: {
    image_id: number
    label_id: number
    time_spent_ms: number
    is_confirm: boolean
    comment?: string
  }) => http.post('/annotations/save', data),
  // v3.4.0: 单图完整修正历史 (含 diff)
  correctionHistory: (imageId: number) =>
    http.get(`/annotations/correction-history/${imageId}`),
  // v3.4.0: 恢复 AI 预测 (撤销人工修正, 状态回 ai_labeled)
  revertToAi: (imageId: number) =>
    http.post(`/annotations/revert-to-ai/${imageId}`),
  // v3.4.0: 数据集级修正统计 (修正率 / 热门修正方向 / 修正原因分布)
  correctionStats: (datasetId: number) =>
    http.get(`/annotations/correction-stats/${datasetId}`),
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
  }),
  // v2.5.46: 分割任务「基础预标注」(torchvision COCO 21 类预训练, 同步接口)
  // - 后端: POST /api/auto-annotate/run-segmentation-pretrained
  // - 对应: backend/app/api/auto_annotate.py:265
  // - 不走 Celery, 直接同步推理 + 写库
  runSegmentationPretrained: (data: {
    dataset_id: number
    model_name: string
    confidence_threshold?: number
    crop_size?: number
    device?: string
    overwrite_existing?: boolean
  }) => http.post<{
    total: number
    auto_labeled: number
    need_human: number
    model_name: string
    threshold: number
    mode: 'sync'
  }>('/auto-annotate/run-segmentation-pretrained', data),
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
    // 后端 /training/start 用 Query(...) 接收参数，走 params 让 axios 自动 URL 编码，
    // 避免 model_name 含特殊字符（& = # 空格 中文）时 URL 损坏。
    http.post('/training/start', null, {
      params: {
        dataset_id: data.dataset_id,
        base_model: data.base_model,
        model_name: data.model_name,
        epochs: data.epochs || 20,
        batch_size: data.batch_size || 32,
        learning_rate: data.learning_rate || 0.0001,
      },
    }),
  // 旧 REST 轮询 (保留兼容, 推荐改用 streamProgress)
  progress: (taskId: string) => http.get(`/training/progress/${taskId}`),
  // 训练历史曲线 (从 Redis 拉, 每个 epoch 结束 worker 会写)
  history: (taskId: string) => http.get(`/training/history/${taskId}`),

  // 列表分页: params = { page, page_size, dataset_id?, state?, task_type?, q? }
  // q: 关键词模糊搜索, 同时匹配 model_name 与 base_model (大小写不敏感)
  // task_type: 任务类型过滤, 支持单值或逗号分隔多值 (e.g. 'classification,detection')
  // 后端返回 { total, items, page, page_size }
  jobs: (params: { page?: number; page_size?: number; dataset_id?: number; state?: string; task_type?: string; q?: string } = {}) =>
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
   * 实现统一收敛到 @/utils/sse 的 createSSEStream（fetch + ReadableStream +
   * AbortController，切页/刷新能立刻断开，不留僵尸连接）。
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
    return createSSEStream(url, callbacks)
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
}

// ============== 导出 ==============
export const exportApi = {
  coco: (datasetId: number) => withToken(`${http.defaults.baseURL}/export/coco/${datasetId}`),
  yolo: (datasetId: number) => withToken(`${http.defaults.baseURL}/export/yolo/${datasetId}`),
  csv: (datasetId: number) => withToken(`${http.defaults.baseURL}/export/csv/${datasetId}`)
}

// ============== 统计分析 ==============
export interface TeamContributorItem {
  user_id: number
  username: string
  annotation_count: number
  total_seconds: number
  avg_seconds_per_annotation: number
}

export interface TeamStatsTimelineData {
  date: string
  count: number
}

export interface TeamStatsAiSaved {
  total_annotations: number
  actual_seconds: number
  avg_seconds_per_image: number
  ai_labeled_count: number
  human_confirmed_count: number
  human_corrected_count: number
  estimated_saved_seconds: number
  estimated_saved_ratio: number
  baseline_seconds_per_image: number
}

export interface TeamStats {
  team_id: number
  team_name: string
  member_count: number
  dataset_count: number
  image_total: number
  labeled_total: number
  unqualified_count: number
  status_counts: Record<string, number>
  category_distribution: Record<string, number>
  top_contributors: TeamContributorItem[]
  timeline: { days: number; data: TeamStatsTimelineData[] }
  ai_saved: TeamStatsAiSaved
}

export const statsApi = {
  overview: () => http.get('/stats/overview'),
  dataset: (datasetId: number) => http.get(`/stats/dataset/${datasetId}`),
  confidence: (datasetId: number) => http.get(`/stats/confidence/${datasetId}`),
  timeline: (datasetId: number, days = 7) =>
    http.get(`/stats/timeline/${datasetId}`, { params: { days } }),
  annotatorEfficiency: (params?: { task_type?: string }) =>
    http.get('/stats/annotator-efficiency', { params }),
  // v3.3.1 L2: 团队统计 (仅团队成员可访问)
  team: (teamId: number, days = 7) =>
    http.get<TeamStats>(`/stats/team/${teamId}`, { params: { days } }),
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
  // ---- 跨图复制建议 (v2.2.0 S9.3) ----
  copySuggestion: (imageId: number) =>
    http.get(`/detection/copy-suggestion/${imageId}`),
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
  // 注意: 后端 /detection/auto-annotate 端点用 Query(...) 接收参数,
  //       必须走 query string, 不能放 body (否则 422)
  startAutoAnnotate: (data: {
    dataset_id: number
    model_version_id: number
    conf_threshold?: number
    iou_threshold?: number
    imgsz?: number
    device?: string
    overwrite_existing?: boolean
  }) => http.post('/detection/auto-annotate', null, { params: data }),
  // v2.3.2: 用预训练 yolov8n/s/m/l/x (无需 ModelVersion)
  startAutoAnnotatePretrained: (data: {
    dataset_id: number
    model_name: string
    conf_threshold?: number
    iou_threshold?: number
    imgsz?: number
    device?: string
    overwrite_existing?: boolean
  }) => http.post('/detection/auto-annotate-pretrained', null, { params: data }),
  // ---- 进度 (旧: 轮询; 新: SSE) ----
  progress: (taskId: string) => http.get(`/detection/progress/${taskId}`),
  history: (taskId: string) => http.get(`/detection/history/${taskId}`),
  // ---- 模型版本管理 ----
  listModels: (params?: { dataset_id?: number; task_type?: string }) =>
    http.get('/detection/models/', { params: params || {} }),
  activateModel: (id: number) => http.post(`/detection/models/${id}/activate`),
  // ---- SSE 实时进度 (复用 @/utils/sse 的 createSSEStream, 与 trainingApi 同源) ----
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
    return createSSEStream(url, callbacks)
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
    http.get(`/segmentation/masks/${imageId}`, {
      params: download ? { download: 'true' } : {},
      responseType: download ? 'blob' : 'json',
    }),
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
  // 注意: 后端 /segmentation/auto-annotate 端点用 Query(...) 接收参数,
  //       必须走 query string, 不能放 body (否则 422)
  startAutoAnnotate: (data: {
    dataset_id: number
    model_version_id: number
    device?: string
    overwrite_existing?: boolean
  }) => http.post('/segmentation/auto-annotate', null, { params: data }),
  // ---- 进度 ----
  progress: (taskId: string) => http.get(`/segmentation/progress/${taskId}`),
  history: (taskId: string) => http.get(`/segmentation/history/${taskId}`),
  // ---- SSE 实时进度 (v2.5.36: 与 detectionApi.streamProgress 对齐) ----
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
    const url = `${baseURL}/segmentation/progress/stream/${taskId}${qs}`
    return createSSEStream(url, callbacks)
  },
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
    withToken(`${http.defaults.baseURL}/export/yolo-det/${datasetId}?val_ratio=${valRatio}`),
  cocoDet: (datasetId: number, includePending = false) =>
    withToken(`${http.defaults.baseURL}/export/coco-det/${datasetId}?include_pending=${includePending}`),
  vocSeg: (datasetId: number, valRatio = 0.2, includePending = false) =>
    withToken(
      `${http.defaults.baseURL}/export/voc-seg/${datasetId}?val_ratio=${valRatio}` +
      `&include_pending=${includePending}`
    ),
  cocoSeg: (datasetId: number, includePending = false) =>
    withToken(`${http.defaults.baseURL}/export/coco-seg/${datasetId}?include_pending=${includePending}`),
}

// ============== 团队管理 (v3.3.0 + v3.3.1 增强) ==============
export interface TeamMemberItem {
  user_id: number
  username: string
  email: string | null
  role: string
  joined_at: string | null
  // v3.3.3: 是否为团队创建者 (所有者)
  // — 后端 list_members 返回, 前端按该字段在「角色」列额外标注「（所有者）」
  is_owner?: boolean
}

export interface TeamItem {
  id: number
  name: string
  slug: string
  description: string | null
  owner_id: number
  max_members: number
  my_role: string
  member_count?: number  // v3.3.1 L3: 仅 list 端点返回
  created_at: string | null
  archived_at?: string | null  // v3.3.1 L3
}

export interface TeamDatasetItem {
  id: number
  name: string
  description: string | null
  task_type: string
  image_count: number
  annotated_count: number
  category_count: number
  status: string
  owner_id: number
  owner_name: string
  my_access: string
  my_access_label: string  // v3.3.2: 中文标签 (可管理/可编辑/可阅读)
}

// v3.3.2: 可共享给团队的数据集 (供团队管理页「共享数据集」按钮使用)
export interface ShareableDatasetItem {
  id: number
  name: string
  task_type: string
  image_count: number
  annotated_count: number
  category_count: number
  status: string
  created_at: string | null
}

export const teamApi = {
  // v3.3.1 L3: 支持分页 + 搜索 + 排序
  list: (params?: {
    page?: number
    page_size?: number
    search?: string
    sort?: 'id_desc' | 'id_asc' | 'name_asc' | 'name_desc'
      | 'member_count_desc' | 'created_asc' | 'created_desc'
    include_archived?: boolean
  }) => http.get<{
    items: TeamItem[]
    page: number
    page_size: number
    total: number
    total_pages: number
    sort: string
    search: string
  }>('/teams', { params }),
  create: (data: { name: string; slug: string; description?: string; max_members?: number }) =>
    http.post('/teams', data),
  get: (id: number) => http.get(`/teams/${id}`),
  // v3.3.1: PATCH 编辑
  update: (id: number, data: { name?: string; description?: string; max_members?: number }) =>
    http.patch(`/teams/${id}`, data),
  remove: (id: number) => http.delete(`/teams/${id}`),
  // v3.3.1 L3: 恢复已归档团队
  restore: (id: number) => http.post(`/teams/${id}/restore`),
  // v3.3.1: 团队级数据集列表
  listDatasets: (id: number) => http.get<{ items: TeamDatasetItem[] }>(`/teams/${id}/datasets`),
  listMembers: (id: number) => http.get<{ items: TeamMemberItem[] }>(`/teams/${id}/members`),
  inviteMember: (id: number, data: { user_id: number; role?: string }) =>
    http.post(`/teams/${id}/members`, data),
  updateMemberRole: (id: number, userId: number, role: string) =>
    http.put(`/teams/${id}/members/${userId}`, { role }),
  removeMember: (id: number, userId: number) =>
    http.delete(`/teams/${id}/members/${userId}`),
  // v3.3.1: 主动退队
  leaveTeam: (id: number) => http.delete(`/teams/${id}/members/me`),
  // v3.3.1: 转让所有权
  transferOwnership: (id: number, newOwnerId: number, confirm: boolean) =>
    http.post(`/teams/${id}/transfer`, { new_owner_id: newOwnerId, confirm }),
  // 数据集共享
  shareDataset: (datasetId: number, teamId: number) =>
    http.post(`/teams/datasets/${datasetId}/share`, null, { params: { team_id: teamId } }),
  unshareDataset: (datasetId: number) =>
    http.delete(`/teams/datasets/${datasetId}/share`),
  // v3.3.2: 团队级「可共享数据集」列表 (团队管理页「共享数据集」按钮)
  listShareableDatasets: (id: number) =>
    http.get<{ items: ShareableDatasetItem[]; total: number }>(
      `/teams/${id}/shareable-datasets`
    ),
  // v3.3.1 L4: 团队活动 Feed
  listActivities: (id: number, params?: { limit?: number; event_type?: string }) =>
    http.get<{
      items: Array<{
        id: number
        user_id: number
        username: string
        event_type: string
        event_label: string
        resource_type: string | null
        resource_id: number | null
        detail: any
        created_at: string | null
      }>
      total: number
      limit: number
      event_type: string | null
    }>(`/teams/${id}/activities`, { params }),
}

// ============== 审计日志 (v3.3.1 L4) ==============
export interface AuditLogItem {
  id: number
  user_id: number
  username: string
  event_type: string
  resource_type: string | null
  resource_id: number | null
  team_id: number | null
  detail: any
  ip_address: string | null
  created_at: string | null
}

export const auditApi = {
  list: (params?: {
    page?: number
    page_size?: number
    event_type?: string
    team_id?: number
    user_id?: number
    resource_type?: string
    start?: string
    end?: string
  }) => http.get<{
    items: AuditLogItem[]
    page: number
    page_size: number
    total: number
    total_pages: number
    filters: Record<string, any>
  }>('/audit-logs', { params }),
}

// ============== 缓存监控 (v3.3.1 L4) ==============
export interface CacheStats {
  hits: number
  misses: number
  total: number
  hit_rate_percent: number
}

export const cacheApi = {
  stats: () => http.get<CacheStats>('/teams/_cache/stats'),
}

// ============== 用户管理 (v3.3.0) ==============
export const userApi = {
  list: () => http.get('/users/'),
  get: (id: number) => http.get(`/users/${id}`),
  create: (data: { username: string; password: string; email?: string; role?: string }) =>
    http.post('/users/', data),
  remove: (id: number) => http.delete(`/users/${id}`),
  activate: (id: number) => http.post(`/users/${id}/activate`),
  deactivate: (id: number) => http.post(`/users/${id}/deactivate`),
  changeRole: (id: number, newRole: string) =>
    http.post(`/users/${id}/role`, { new_role: newRole }),
  resetPassword: (id: number, newPassword: string) =>
    http.post(`/users/${id}/reset-password`, { new_password: newPassword }),
  // 个人中心
  getProfile: () => http.get('/users/me/profile'),
  updateProfile: (data: { email?: string }) => http.put('/users/me/profile', data),
  changePassword: (data: { old_password: string; new_password: string }) =>
    http.post('/users/me/change-password', data),
}
