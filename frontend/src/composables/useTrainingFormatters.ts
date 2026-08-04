/**
 * useTrainingFormatters - 训练页格式化工具 composable
 *
 * v3.0.0 Phase J 拆分: 从 Training/index.vue 抽离
 * - formatTime: 智能识别 naive/aware datetime, 统一按本地时区显示
 * - formatDurationSmart: 秒数 → 人类可读 (Xs / Xm Ys / Xh Ym)
 * - deviceTagType / deviceShortLabel / formatDeviceTooltip: 设备信息格式化
 * - stateLabel: 状态码 → 中文
 * - progressStatus: 状态 → el-progress 状态 (success/exception/warning)
 * - genDefaultModelName: 生成默认 model_name
 */
export function useTrainingFormatters() {
  /**
   * 格式化时间为本地时区 (CST/GMT+8) 显示
   * 后端 datetime 序列化规则:
   * - 历史数据: naive ISO 字符串 (无 tz 标记), 浏览器按本地时区解析, 相差 8 小时
   * - 新数据: 带 "Z" 或 "+00:00" tz 标记, 浏览器正确按 UTC 解析
   *
   * 修复策略:
   * - 带 "Z" 或 "+/-HH:MM" → 信任浏览器解析
   * - 不带 → 默认当作 UTC 处理, 手动追加 "Z" 后再解析
   */
  const formatTime = (iso: string | null | undefined): string => {
    if (!iso) return '-'
    const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(iso)
    const parseable = hasTz ? iso : `${iso}Z`
    try {
      const d = new Date(parseable)
      if (isNaN(d.getTime())) return iso
      return d.toLocaleString('zh-CN', { hour12: false })
    } catch {
      return iso
    }
  }

  /**
   * 智能格式化耗时 (秒 → 人类可读)
   *  < 60s    → "42.3s"
   *  < 3600s  → "5m 23s"
   *  >= 3600s → "1h 12m"
   */
  const formatDurationSmart = (s: number): string => {
    if (!s || s < 0) return '-'
    if (s < 60) return `${s.toFixed(1)}s`
    const m = Math.floor(s / 60)
    if (m < 60) {
      const rem = Math.floor(s % 60)
      return `${m}m ${rem}s`
    }
    const h = Math.floor(m / 60)
    const remM = m % 60
    return `${h}h ${remM}m`
  }

  // ============== 设备信息格式化 ==============
  // device_type: "cuda" | "mps" | "cpu" | undefined (老数据可能没)
  const deviceTagType = (t: string | null | undefined) => {
    if (t === 'cuda') return 'success'   // GPU 绿
    if (t === 'mps') return 'warning'    // MPS 黄
    if (t === 'cpu') return 'info'       // CPU 灰
    return 'info'
  }
  const deviceShortLabel = (t: string | null | undefined, name: string | null | undefined) => {
    const type = (t || 'cpu').toUpperCase()
    if (!name) return type
    // 名字截断: NVIDIA GeForce RTX 4090 -> RTX 4090 (去掉前缀)
    const short = name.replace(/^NVIDIA\s+/i, '').replace(/^GeForce\s+/i, '')
    return `${type} · ${short}`
  }
  const formatDeviceTooltip = (info: any) => {
    if (!info || typeof info !== 'object') return ''
    const lines: string[] = []
    lines.push(`设备: ${info.device_name || '?'}`)
    if (info.device_type) lines.push(`类型: ${info.device_type.toUpperCase()}`)
    if (info.cuda_version) lines.push(`CUDA: ${info.cuda_version}`)
    if (info.cudnn_version) lines.push(`cuDNN: ${info.cudnn_version}`)
    if (info.gpu_memory_total_mb) lines.push(`GPU 显存: ${(info.gpu_memory_total_mb / 1024).toFixed(1)} GB`)
    if (info.gpu_peak_mb) lines.push(`训练峰值显存: ${(info.gpu_peak_mb / 1024).toFixed(1)} GB`)
    if (info.cpu_count) lines.push(`CPU 核数: ${info.cpu_count}`)
    if (info.ram_gb) lines.push(`RAM: ${info.ram_gb} GB`)
    if (info.torch_version) lines.push(`PyTorch: ${info.torch_version}`)
    if (info.python_version) lines.push(`Python: ${info.python_version}`)
    if (info.os_platform) lines.push(`系统: ${info.os_platform}`)
    if (info.fallback_reason) lines.push(`⚠️ 退回: ${info.fallback_reason}`)
    return lines.join('\n')
  }

  // ============== 状态文案/类型 ==============
  const stateLabel = (s: string) => {
    const m: Record<string, string> = {
      PENDING: '等待中', PROGRESS: '训练中', SUCCESS: '已完成',
      FAILURE: '失败', REVOKED: '已取消', PAUSED: '已暂停',
    }
    return m[s] || s
  }
  const progressStatus = (state: string) => {
    if (state === 'SUCCESS') return 'success'
    if (state === 'FAILURE' || state === 'REVOKED') return 'exception'
    if (state === 'PAUSED') return 'warning'
    return undefined
  }

  // ============== 默认 model_name 生成 (v3.5.1 重新启用) ==============
  // 规则 (与后端 _default_model_name 完全一致, 前端无 DB 查重能力, 重名时由后端兜底):
  //   - 新建任务: `${baseModel}_${ts}` (e.g. resnet50_1701234567)
  //   - 再训练任务: `${baseModel}_r_${ts}` (e.g. resnet50_r_1701234567)
  //   - 时间戳: 10位秒级 (避免 13位毫秒太长, 也保证一年内不重复)
  //
  // 历史:
  //   - v3.0.0 改版曾废弃前端拼接, 全部由后端接管; 但用户反馈主动生成名称更直观
  //   - v3.5.1 重新启用前端「生成」按钮, 仅生成默认名 (后端仍做查重/截断兜底)
  // @see backend/app/tasks/api/training/start.py::_default_model_name
  const genDefaultModelName = (baseModel: string, retrain = false): string => {
    const ts = Math.floor(Date.now() / 1000) % 10000000000
    const suffix = retrain ? `_r_${ts}` : `_${ts}`
    return `${baseModel}${suffix}`
  }

  return {
    formatTime,
    formatDurationSmart,
    deviceTagType,
    deviceShortLabel,
    formatDeviceTooltip,
    stateLabel,
    progressStatus,
    genDefaultModelName,
  }
}
