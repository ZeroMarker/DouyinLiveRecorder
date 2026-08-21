// 与 webui/app.py 各 API 响应结构一一对应
export interface PlatformInfo {
  name: string
  hosts: string[]
  overseas: boolean
  url_template?: string
  id_placeholder?: string
}

export interface StatusInfo {
  running: boolean
  version: string
  recording_count: number
  task_count: number
  disk_free_gb: number
  platform_count: number
}

export type TaskStatus = 'recording' | 'waiting' | 'offline' | 'error' | 'stopped' | 'unknown'

export interface TaskItem {
  url: string
  quality: string
  name: string
  commented: boolean
  platform: string
  status: TaskStatus
  anchor: string
  recording_seconds: number
  file: string
  message: string
  last_check: number
}

export interface LogEntry {
  time: number
  level: string
  message: string
}

export interface VideoFile {
  name: string
  path: string
  size: number
  mtime: number
}

export interface MetaInfo {
  downloads_dir: string
  config_file: string
  url_config_file: string
}
