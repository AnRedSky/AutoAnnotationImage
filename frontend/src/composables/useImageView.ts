import { ref, nextTick } from 'vue'

/**
 * useImageView - 图像网格/列表视图模式 + 图片详情查看器状态
 *
 * v3.0.0 Phase L 拆分: 从 DatasetDetail/index.vue 抽离
 *
 * 职责:
 * - 维护视图模式 (grid / list)
 * - 维护网格视图的图片尺寸 (small / medium / large)
 * - 维护图片详情弹窗状态 (AnnotationViewer)
 * - 修复: v-model 必须是 Boolean, 不能复用 number 类型的 imageId
 *   拆成 viewerOpen (boolean) + viewerImageId (number | null) 两个状态
 * - 上传弹窗状态 (UploadQueue 共用)
 * - 视图切换时保持滚动位置 (切换前保存 scrollTop, 切换后 nextTick 恢复)
 */
export type ViewMode = 'grid' | 'list'
export type GridSize = 'small' | 'medium' | 'large'

export function useImageView() {
  // 视图模式: grid (默认) / list
  const viewMode = ref<ViewMode>('grid')

  // 网格视图图片尺寸: small (小) / medium (中, 默认) / large (大)
  // - large: 每行少几张, 卡片大, 适合精细查看
  // - medium: 默认尺寸
  // - small: 每行多几张, 卡片小, 适合快速浏览
  const gridSize = ref<GridSize>('medium')

  // 详情弹窗状态 (AnnotationViewer)
  // 修复: v-model 必须是 Boolean, 不能复用 number 类型的 imageId
  // 拆成 viewerOpen (boolean) + viewerImageId (number | null) 两个状态
  const viewerOpen = ref(false)
  const viewerImageId = ref<number | null>(null)

  // 上传弹窗状态 (UploadQueue)
  const uploadOpen = ref(false)

  // 视图切换时的滚动位置: 切换前保存到 savedScrollTop,
  // 切换后在 nextTick 恢复 (保证用户浏览位置不丢)
  const savedScrollTop = ref(0)

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
   * 切换视图模式 (保持滚动位置)
   * - 调用方传入 imageAreaRef (图片展示区的 DOM 元素 ref)
   * - 切换前: 保存当前 scrollTop
   * - 切换后: nextTick 等 DOM 更新完成, 恢复 scrollTop
   */
  async function setViewMode(mode: ViewMode, imageAreaRef?: HTMLElement | null) {
    if (mode === viewMode.value) return
    if (imageAreaRef) {
      savedScrollTop.value = imageAreaRef.scrollTop
    }
    viewMode.value = mode
    // 等下一帧 DOM 重新渲染后恢复滚动位置
    await nextTick()
    if (imageAreaRef) {
      imageAreaRef.scrollTop = savedScrollTop.value
    }
  }

  return {
    viewMode, gridSize, viewerOpen, viewerImageId, uploadOpen,
    savedScrollTop,
    openViewer, closeViewer, openUpload, closeUpload, setViewMode,
  }
}
