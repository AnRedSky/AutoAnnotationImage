import { ref } from 'vue'

/**
 * useImageView - 图像网格/列表视图模式 + 图片详情查看器状态
 *
 * v3.0.0 Phase L 拆分: 从 DatasetDetail/index.vue 抽离
 *
 * 职责:
 * - 维护视图模式 (grid / list)
 * - 维护图片详情弹窗状态 (AnnotationViewer)
 * - 修复: v-model 必须是 Boolean, 不能复用 number 类型的 imageId
 *   拆成 viewerOpen (boolean) + viewerImageId (number | null) 两个状态
 * - 上传弹窗状态 (UploadQueue 共用)
 */
export type ViewMode = 'grid' | 'list'

export function useImageView() {
  // 视图模式: grid (默认) / list
  const viewMode = ref<ViewMode>('grid')

  // 详情弹窗状态 (AnnotationViewer)
  // 修复: v-model 必须是 Boolean, 不能复用 number 类型的 imageId
  // 拆成 viewerOpen (boolean) + viewerImageId (number | null) 两个状态
  const viewerOpen = ref(false)
  const viewerImageId = ref<number | null>(null)

  // 上传弹窗状态 (UploadQueue)
  const uploadOpen = ref(false)

  /**
   * 打开指定 id 的图片详情
   */
  function openViewer(imgId: number) {
    viewerImageId.value = imgId
    viewerOpen.value = true
  }

  /**
   * 关闭详情弹窗
   */
  function closeViewer() {
    viewerOpen.value = false
    viewerImageId.value = null
  }

  /**
   * 打开上传弹窗
   */
  function openUpload() {
    uploadOpen.value = true
  }

  /**
   * 关闭上传弹窗
   */
  function closeUpload() {
    uploadOpen.value = false
  }

  /**
   * 切换视图模式
   */
  function setViewMode(mode: ViewMode) {
    viewMode.value = mode
  }

  return {
    viewMode, viewerOpen, viewerImageId, uploadOpen,
    openViewer, closeViewer, openUpload, closeUpload, setViewMode,
  }
}
