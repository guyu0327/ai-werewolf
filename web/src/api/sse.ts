/** 通用 SSE 消费（fetch + getReader）：支持 POST 场景与自定义头，比 EventSource 灵活。 */

export interface SseBlock {
  event: string
  data: string
}

function parseBlock(block: string): SseBlock | null {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }
  if (dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}

/**
 * 订阅 SSE 端点，逐块产出解析后的事件。由调用方决定何时终止（通过 signal 或返回 true 中断）。
 */
export async function* sseStream(url: string, headers: Record<string, string> = {}, signal?: AbortSignal) {
  const res = await fetch(url, { headers, signal })
  if (!res.ok || !res.body) throw new Error(`SSE 连接失败 (${res.status})`)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const block = parseBlock(buffer.slice(0, idx))
        buffer = buffer.slice(idx + 2)
        if (block) yield block
      }
    }
  } finally {
    reader.releaseLock()
  }
}
