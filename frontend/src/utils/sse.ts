/**
 * SSE 流式订阅公共工具
 * ==================================================
 * 之前 trainingApi.streamProgress / detectionApi.streamProgress 各自手写了一份
 * 几乎相同的 fetch + ReadableStream + 帧解析逻辑 (~100 行重复)。
 * 这里抽取为单一实现，调用方只需传入不同 URL。
 *
 * 为什么不用 EventSource:
 *   EventSource 不支持自定义 header，鉴权只能走 query。本接口后端为可选鉴权，
 *   仍保留 query token；这里用 fetch + ReadableStream 是为了拿到 AbortController
 *   的精确控制（切页/刷新能立刻断开，不留僵尸连接）。
 */

export interface SSECallbacks {
  /** 每帧 data: 事件回调 */
  onMessage: (data: any) => void
  /** 服务端推完 end 事件后回调（训练已结束） */
  onComplete?: () => void
  /** 连接/解析异常回调 */
  onError?: (err: Error) => void
}

/** 终态 state 集合，命中即视为完成并关闭流 */
const TERMINAL_STATES = new Set(['SUCCESS', 'FAILURE', 'REVOKED'])

/**
 * 创建一个 SSE 订阅流。
 * @param url 完整的 SSE 端点 URL（含 query token）
 * @param callbacks 消息/完成/异常回调
 * @returns cancel() 函数，调用后立即关闭 fetch + abort
 */
export function createSSEStream(
  url: string,
  callbacks: SSECallbacks,
): () => void {
  const controller = new AbortController()
  let finished = false

  const finish = (code: 'complete' | 'error', err?: Error) => {
    if (finished) return
    finished = true
    if (code === 'complete') callbacks.onComplete?.()
    else if (err) callbacks.onError?.(err)
    try { controller.abort() } catch { /* noop */ }
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
      if (e?.name !== 'AbortError') {
        finish('error', e instanceof Error ? e : new Error(String(e)))
      }
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

        // SSE 帧以 \n\n 结束，用换行切分后保留最后一段（可能不完整）
        let idx: number
        // eslint-disable-next-line no-cond-assign
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const rawFrame = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)
          // 空帧 / 注释帧（`: keepalive ...`）直接跳过
          if (!rawFrame || rawFrame.startsWith(':')) continue

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
          let payload: any
          try { payload = JSON.parse(dataLines.join('\n')) } catch { continue }

          callbacks.onMessage(payload)

          // 终端态由事件或 payload.state 双重判定
          if (eventName === 'end' || TERMINAL_STATES.has(payload?.state)) {
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
      try { controller.abort() } catch { /* noop */ }
    }
  }
}
