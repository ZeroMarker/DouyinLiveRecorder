// 传输层：Tauri 环境走 @tauri-apps/plugin-http（Rust 侧发请求，
// 无 CORS/CSP 平台差异）；纯浏览器 dev 模式走原生 fetch（后端固定端口）。
import { fetch as tauriFetch } from '@tauri-apps/plugin-http'
import { invoke } from '@tauri-apps/api/core'

let baseUrl = ''
let inTauri = false
let inited = false

/** 初始化传输层：探测运行环境并获取 sidecar 后端地址。
 *  后端未就绪时返回 false，调用方应稍后重试（Tauri 壳与 sidecar 异步启动）。 */
export async function initApi(): Promise<boolean> {
  if (inited) return true
  // Tauri 运行时注入的全局标记，存在即运行在桌面壳内（结构由 Tauri 保证）
  const tauriInternals = (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__
  inTauri = !!tauriInternals
  if (!inTauri) {
    // 浏览器 dev 模式：手动启动的后端固定端口，可用 VITE_BACKEND_URL 覆盖
    baseUrl = (import.meta.env.VITE_BACKEND_URL as string | undefined) || 'http://127.0.0.1:8000'
    inited = true
    return true
  }
  try {
    const url = await invoke<string>('backend_url')
    if (!url) return false
    baseUrl = url
    inited = true
    return true
  } catch {
    return false
  }
}

export function isTauri(): boolean {
  return inTauri
}

function doFetch(input: string, init?: RequestInit): Promise<Response> {
  const url = baseUrl + input
  if (inTauri) return tauriFetch(url, init)
  return fetch(url, init)
}

/** 带 15s 超时的请求封装，与原 WebUI 行为一致。 */
export async function request(path: string, opts: RequestInit = {}): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 15000)
  let r: Response
  try {
    r = await doFetch(path, { ...opts, signal: controller.signal })
  } catch (e) {
    if (e instanceof Error && e.name === 'AbortError') throw new Error('请求超时')
    throw e
  } finally {
    clearTimeout(timer)
  }
  if (!r.ok) {
    throw new Error((await extractErrorMessage(r)) || `HTTP ${r.status}`)
  }
  return r
}

/** FastAPI 错误体：{detail: string} 或校验错误 {detail: [{loc,msg,type},...]}。 */
async function extractErrorMessage(r: Response): Promise<string> {
  let body: unknown
  try {
    body = await r.json()
  } catch {
    return r.statusText // 非 JSON 错误响应
  }
  if (!(body && typeof body === 'object' && 'detail' in body)) return r.statusText
  const detail: unknown = body.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const msgs = detail.map((item) =>
      item && typeof item === 'object' && 'msg' in item && typeof item.msg === 'string'
        ? item.msg
        : String(item),
    )
    return msgs.filter(Boolean).join('; ')
  }
  if (detail !== null && detail !== undefined) return String(detail)
  return r.statusText
}

export async function api<T = unknown>(path: string, opts?: RequestInit): Promise<T> {
  return (await request(path, opts)).json() as Promise<T>
}

export async function apiText(path: string, opts?: RequestInit): Promise<string> {
  return (await request(path, opts)).text()
}

/** 播放地址：直接指向 sidecar 后端（浏览器/Tauri 统一），CSP 已放行 media-src。 */
export function playUrl(path: string): string {
  const encoded = path.split('/').map(encodeURIComponent).join('/')
  return `${baseUrl}/api/videos/play/${encoded}`
}

/** 后端基地址（浏览器 dev 模式下载等直链需要）。 */
export function backendBase(): string {
  return baseUrl
}
