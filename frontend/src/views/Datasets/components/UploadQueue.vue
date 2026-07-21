<script setup lang="ts">
/**
 * UploadQueue - 上传队列组件
 * 一次选择多文件 → 顺序上传 → 实时进度 → 失败重试
 *
 * Props:
 *   datasetId: number      目标数据集 id
 *   status?: 'pending' | 'done' | 'failed' | 'duplicate' | 'uploading'  单条状态
 * Emits:
 *   uploaded(result)       上传完成事件 (含 {uploaded, duplicates, total, items})
 */
import { ref, computed, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  UploadFilled, Delete, Refresh, Check, Document, Picture, FolderOpened
} from '@element-plus/icons-vue'
import { imageApi } from '@/api'

interface QueueItem {
  uid: number
  file: File
  status: 'pending' | 'uploading' | 'done' | 'failed' | 'duplicate'
  loaded: number
  total: number
  speed: number        // bytes/s
  startTime: number
  errorMsg: string
  duplicate: boolean
  /** 来自文件夹时记录所在子目录 (webkitRelativePath 的目录部分)，便于识别 */
  folder: string
}

const props = defineProps<{ datasetId: number | null; autoStart?: boolean }>()
const emit = defineEmits<{ uploaded: [result: any] }>()

const queue = ref<QueueItem[]>([])
const fileInput = ref<HTMLInputElement | null>(null)
const dirInput = ref<HTMLInputElement | null>(null)
const isUploading = ref(false)
let uidCounter = 0

const stats = computed(() => {
  const s = { total: queue.value.length, done: 0, failed: 0, pending: 0, duplicate: 0 }
  for (const it of queue.value) {
    if (it.status === 'done') s.done++
    else if (it.status === 'failed') s.failed++
    else if (it.status === 'duplicate') s.duplicate++
    else s.pending++
  }
  return s
})

const totalLoaded = computed(() => queue.value.reduce((acc, it) => acc + it.loaded, 0))
const totalSize = computed(() => queue.value.reduce((acc, it) => acc + it.total, 0))
const overallPct = computed(() => {
  if (totalSize.value === 0) return 0
  return Math.round((totalLoaded.value / totalSize.value) * 100)
})

function pickFiles() {
  fileInput.value?.click()
}

function pickFolder() {
  dirInput.value?.click()
}

function onFileChange(e: Event) {
  const target = e.target as HTMLInputElement
  if (!target.files) return
  addFiles(Array.from(target.files))
  target.value = ''  // 清空以便下次选相同文件
}

function onDirChange(e: Event) {
  const target = e.target as HTMLInputElement
  if (!target.files || target.files.length === 0) {
    target.value = ''
    return
  }
  // webkitdirectory 会把目录下（递归）所有文件塞进来，浏览器不一定遵守 accept
  // 因此前端再做一次图片过滤 + 子目录归并
  const files = Array.from(target.files)
  const imageFiles = files.filter(isImage)
  const skipped = files.length - imageFiles.length
  if (imageFiles.length === 0) {
    ElMessage.warning('所选文件夹中未找到任何图片文件')
  } else {
    if (skipped > 0) {
      ElMessage.info(`已自动加入 ${imageFiles.length} 张图片，跳过 ${skipped} 个非图片文件`)
    }
    addFiles(imageFiles)
  }
  target.value = ''
}

async function onDrop(e: DragEvent) {
  e.preventDefault()
  if (!e.dataTransfer) return
  // 优先识别文件夹拖拽 (DataTransferItem + webkitGetAsEntry)
  const items = e.dataTransfer.items
  if (items && items.length > 0) {
    const collected: File[] = []
    let hasDirectory = false
    const tasks: Promise<void>[] = []
    for (let i = 0; i < items.length; i++) {
      const item = items[i]
      if (item.kind !== 'file') continue
      const entry = (item as any).webkitGetAsEntry?.()
      if (entry) {
        if (entry.isDirectory) {
          hasDirectory = true
          tasks.push(readEntry(entry, collected))
        } else if (entry.isFile) {
          tasks.push(
            new Promise<void>((resolve) => {
              entry.file(
                (f: File) => { collected.push(f); resolve() },
                () => resolve()
              )
            })
          )
        }
      } else {
        // 降级：无法获取 entry 时，尝试用 dataTransfer.files
        const f = item.getAsFile?.()
        if (f) collected.push(f)
      }
    }
    if (hasDirectory) {
      await Promise.all(tasks)
      finalizeDrop(collected)
      return
    }
    if (collected.length > 0) {
      finalizeDrop(collected)
      return
    }
  }
  // 普通文件拖拽
  finalizeDrop(Array.from(e.dataTransfer.files))
}

function finalizeDrop(rawFiles: File[]) {
  if (rawFiles.length === 0) {
    ElMessage.warning('拖入内容中没有可识别的文件')
    return
  }
  const imageFiles = rawFiles.filter(isImage)
  const skipped = rawFiles.length - imageFiles.length
  if (imageFiles.length === 0) {
    ElMessage.warning('拖入内容中没有图片文件')
    return
  }
  if (skipped > 0) {
    ElMessage.info(`已自动加入 ${imageFiles.length} 张图片，跳过 ${skipped} 个非图片文件`)
  }
  addFiles(imageFiles)
}

/**
 * 递归读取目录条目，收集所有文件到 out 数组
 * 对每收集到的文件补上 webkitRelativePath（拖拽 API 不会自动设置）
 */
function readEntry(entry: any, out: File[]): Promise<void> {
  return new Promise((resolve) => {
    if (entry.isFile) {
      entry.file(
        (f: File) => {
          if (!f.webkitRelativePath) {
            const rel = (entry.fullPath || '/' + entry.name).replace(/^\/+/, '') + '/' + f.name
            trySetRelativePath(f, rel)
          }
          out.push(f)
          resolve()
        },
        () => resolve()
      )
      return
    }
    if (entry.isDirectory) {
      const reader = entry.createReader()
      const readBatch = () => {
        reader.readEntries(
          async (entries: any[]) => {
            if (entries.length === 0) { resolve(); return }
            await Promise.all(entries.map((c) => readEntry(c, out)))
            // readEntries 一次最多返回 100 条, 需循环读完
            readBatch()
          },
          () => resolve()
        )
      }
      readBatch()
      return
    }
    resolve()
  })
}

function trySetRelativePath(file: File, relPath: string) {
  try {
    Object.defineProperty(file, 'webkitRelativePath', {
      value: relPath,
      configurable: true
    })
  } catch {
    /* readonly 时忽略，队列里只用 file.name 也能看 */
  }
}

function addFiles(files: File[]) {
  for (const f of files) {
    const folder = extractFolder(f)
    queue.value.push({
      uid: ++uidCounter,
      file: f,
      status: 'pending',
      loaded: 0,
      total: f.size,
      speed: 0,
      startTime: 0,
      errorMsg: '',
      duplicate: false,
      folder
    })
  }
  if (props.autoStart !== false) startAll()
}

function extractFolder(f: File): string {
  const rel = (f as any).webkitRelativePath as string | undefined
  if (!rel || !rel.includes('/')) return ''
  return rel.slice(0, rel.lastIndexOf('/'))
}

async function startAll() {
  if (isUploading.value) return
  if (!props.datasetId) {
    ElMessage.warning('请先选择数据集')
    return
  }
  isUploading.value = true
  for (const item of queue.value) {
    if (item.status === 'pending') {
      await uploadOne(item)
    }
  }
  isUploading.value = false
  emit('uploaded', stats.value)
  if (stats.value.done > 0) {
    ElMessage.success(
      `上传完成: 成功 ${stats.value.done} 张` +
      (stats.value.duplicate > 0 ? `, 重复 ${stats.value.duplicate} 张` : '') +
      (stats.value.failed > 0 ? `, 失败 ${stats.value.failed} 张` : '')
    )
  }
}

async function uploadOne(item: QueueItem) {
  if (!props.datasetId) return
  item.status = 'uploading'
  item.startTime = Date.now()
  item.errorMsg = ''
  let lastLoaded = 0
  let lastTs = Date.now()

  const fd = new FormData()
  fd.append('files', item.file)

  try {
    const res: any = await imageApi.upload(props.datasetId, fd, (e) => {
      item.loaded = e.loaded
      // 计算瞬时速度
      const now = Date.now()
      const dt = (now - lastTs) / 1000
      if (dt > 0.2) {
        item.speed = (e.loaded - lastLoaded) / dt
        lastLoaded = e.loaded
        lastTs = now
      }
    })
    // 单文件上传返回 {total, uploaded, duplicates, items}
    const resultItem = res?.items?.[0]
    if (resultItem?.duplicate) {
      item.status = 'duplicate'
      item.duplicate = true
    } else {
      item.status = 'done'
      item.loaded = item.total
    }
  } catch (e: any) {
    item.status = 'failed'
    item.errorMsg = e?.response?.data?.detail || e?.message || '上传失败'
  }
}

async function retryItem(item: QueueItem) {
  if (!props.datasetId) return
  item.status = 'pending'
  item.errorMsg = ''
  item.loaded = 0
  isUploading.value = true
  await uploadOne(item)
  isUploading.value = false
}

async function retryAllFailed() {
  const failed = queue.value.filter((it) => it.status === 'failed')
  if (failed.length === 0) return
  for (const item of failed) {
    item.status = 'pending'
    item.errorMsg = ''
    item.loaded = 0
  }
  await startAll()
}

function removeItem(item: QueueItem) {
  if (item.status === 'uploading') {
    ElMessage.warning('正在上传中, 请稍候')
    return
  }
  queue.value = queue.value.filter((q) => q.uid !== item.uid)
}

async function clearDone() {
  const toRemove = queue.value.filter((it) => it.status === 'done' || it.status === 'duplicate')
  if (toRemove.length === 0) return
  try {
    await ElMessageBox.confirm(`确定清除已完成的 ${toRemove.length} 条记录?`, '提示', { type: 'info' })
    queue.value = queue.value.filter((q) => q.status !== 'done' && q.status !== 'duplicate')
  } catch { /* user cancelled */ }
}

function clearAll() {
  if (queue.value.some((it) => it.status === 'uploading')) {
    ElMessage.warning('正在上传中, 不能清空')
    return
  }
  queue.value = []
}

function formatBytes(b: number): string {
  if (!b) return '0 B'
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`
  return `${(b / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function formatSpeed(b: number): string {
  return formatBytes(b) + '/s'
}

function etaOf(item: QueueItem): string {
  if (item.status !== 'uploading' || item.speed <= 0) return '-'
  const remain = item.total - item.loaded
  const sec = remain / item.speed
  if (sec < 60) return `${Math.ceil(sec)}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${Math.ceil(sec % 60)}s`
  return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`
}

function statusType(s: QueueItem['status']): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  switch (s) {
    case 'done': return 'success'
    case 'duplicate': return 'warning'
    case 'failed': return 'danger'
    case 'uploading': return 'primary'
    default: return 'info'
  }
}

function statusLabel(s: QueueItem['status']): string {
  return { pending: '等待', uploading: '上传中', done: '完成', failed: '失败', duplicate: '重复' }[s]
}

function isImage(f: File): boolean {
  return (f.type || '').startsWith('image/') || /\.(jpe?g|png|webp|bmp|gif)$/i.test(f.name)
}

onBeforeUnmount(() => {
  // 组件销毁, 不中断 in-flight 上传
})

defineExpose({ pickFiles, startAll, retryAllFailed, clearAll })
</script>

<template>
  <div>
    <!-- 拖放 / 选择区 -->
    <div
      class="upload-drop"
      @dragover.prevent @dragenter.prevent @drop="onDrop"
    >
      <el-icon :size="48" color="#409eff"><UploadFilled /></el-icon>
      <div class="upload-text">点击下方按钮选择文件 / 文件夹，或直接拖入（支持多张或整个文件夹，系统自动 SHA-256 去重）</div>
      <div class="upload-sub">单张最大 20MB · 推荐 jpg/png/webp</div>
      <div class="upload-buttons" @click.stop>
        <el-button type="primary" @click="pickFiles">选择文件</el-button>
        <el-button :icon="FolderOpened" @click="pickFolder">选择文件夹</el-button>
      </div>
      <input ref="fileInput" type="file" multiple accept="image/*"
        style="display:none;" @change="onFileChange" />
      <input ref="dirInput" type="file" webkitdirectory directory multiple
        style="display:none;" @change="onDirChange" />
    </div>

    <!-- 总览 -->
    <div v-if="queue.length > 0" class="upload-summary">
      <el-row :gutter="12" align="middle">
        <el-col :span="3">
          <el-statistic title="总文件" :value="stats.total" />
        </el-col>
        <el-col :span="3">
          <el-statistic title="完成" :value="stats.done" :value-style="{ color: '#67c23a' }" />
        </el-col>
        <el-col :span="3">
          <el-statistic title="重复" :value="stats.duplicate" :value-style="{ color: '#e6a23c' }" />
        </el-col>
        <el-col :span="3">
          <el-statistic title="失败" :value="stats.failed" :value-style="{ color: '#f56c6c' }" />
        </el-col>
        <el-col :span="12">
          <el-progress
            :percentage="overallPct"
            :status="stats.failed > 0 ? 'exception' : (isUploading ? undefined : (stats.done + stats.duplicate === stats.total && stats.total > 0 ? 'success' : undefined))"
            :stroke-width="14"
            :format="(p: number) => `${formatBytes(totalLoaded)} / ${formatBytes(totalSize)} (${p}%)`"
          />
        </el-col>
      </el-row>
      <div class="upload-actions">
        <el-button size="small" type="primary" :loading="isUploading"
          :disabled="stats.pending === 0" @click="startAll">
          继续上传 ({{ stats.pending }})
        </el-button>
        <el-button size="small" :disabled="stats.failed === 0" @click="retryAllFailed">
          <el-icon><Refresh /></el-icon> 重试失败 ({{ stats.failed }})
        </el-button>
        <el-button size="small" :disabled="stats.done + stats.duplicate === 0" @click="clearDone">
          <el-icon><Delete /></el-icon> 清除已完成
        </el-button>
        <el-button size="small" :disabled="queue.length === 0 || isUploading" @click="clearAll">
          <el-icon><Delete /></el-icon> 清空队列
        </el-button>
      </div>
    </div>

    <!-- 队列列表 -->
    <div v-if="queue.length > 0" class="upload-list">
      <div v-for="item in queue" :key="item.uid" class="upload-item">
        <el-icon :size="24" :color="isImage(item.file) ? '#409eff' : '#909399'">
          <component :is="isImage(item.file) ? Picture : Document" />
        </el-icon>
        <div class="upload-item-body">
          <div class="upload-item-name">
            <span>{{ item.file.name }}</span>
            <el-tag v-if="item.folder" :type="statusType(item.status)" size="small" effect="plain"
              style="margin-left: 8px;">
              <el-icon style="vertical-align: -2px;"><FolderOpened /></el-icon>
              {{ item.folder }}
            </el-tag>
            <el-tag v-else :type="statusType(item.status)" size="small" style="margin-left: 8px;">
              {{ statusLabel(item.status) }}
            </el-tag>
          </div>
          <div class="upload-item-meta">
            {{ formatBytes(item.file.size) }}
            <span v-if="item.status === 'uploading' && item.speed > 0" style="margin-left: 8px;">
              · {{ formatSpeed(item.speed) }} · 剩余 {{ etaOf(item) }}
            </span>
            <span v-if="item.errorMsg" style="margin-left: 8px; color: #f56c6c;">
              · {{ item.errorMsg }}
            </span>
          </div>
          <el-progress
            v-if="item.status !== 'duplicate'"
            :percentage="item.total > 0 ? Math.round((item.loaded / item.total) * 100) : 0"
            :status="item.status === 'failed' ? 'exception' : item.status === 'done' ? 'success' : undefined"
            :stroke-width="6" :show-text="false"
            style="margin-top: 2px;"
          />
          <el-progress
            v-else
            :percentage="100"
            :stroke-width="6" :show-text="false"
            status="warning"
            style="margin-top: 2px;"
          />
        </div>
        <div class="upload-item-actions">
          <el-button v-if="item.status === 'failed'" size="small" type="primary" link @click="retryItem(item)">
            <el-icon><Refresh /></el-icon> 重试
          </el-button>
          <el-button size="small" type="danger" link :disabled="item.status === 'uploading'" @click="removeItem(item)">
            <el-icon><Delete /></el-icon>
          </el-button>
        </div>
      </div>
    </div>

    <el-empty v-else description="还没有文件, 拖拽图片到上方区域开始上传" :image-size="80" />
  </div>
</template>

<style scoped>
.upload-drop {
  border: 2px dashed #d9d9d9;
  border-radius: 8px;
  padding: 32px 16px;
  text-align: center;
  cursor: pointer;
  background: #fafafa;
  transition: all 0.2s;
}
.upload-drop:hover { border-color: #409eff; background: #ecf5ff; }
.upload-text { font-size: 14px; color: #606266; margin-top: 12px; }
.upload-sub { font-size: 12px; color: #909399; margin-top: 4px; }
.upload-buttons { margin-top: 16px; display: flex; gap: 12px; justify-content: center; }

.upload-summary {
  margin-top: 16px;
  padding: 12px 16px;
  background: #f5f7fa;
  border-radius: 4px;
}
.upload-actions {
  margin-top: 8px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.upload-list {
  margin-top: 12px;
  max-height: 360px;
  overflow-y: auto;
  border: 1px solid #ebeef5;
  border-radius: 4px;
}
.upload-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-bottom: 1px solid #ebeef5;
}
.upload-item:last-child { border-bottom: none; }
.upload-item-body { flex: 1; min-width: 0; }
.upload-item-name {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  display: flex;
  align-items: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.upload-item-meta {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
.upload-item-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
</style>
