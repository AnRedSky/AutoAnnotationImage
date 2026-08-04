<!--
  CorrectionCommentInput.vue (v3.4.0)
  ====================================================
  业务组件: 修正原因输入 (选填, 默认空)
  路径: src/components/annotation-business/CorrectionCommentInput.vue
  - 6 种预设原因 (AI 误判 / 类别模糊 / 遮挡 / 多目标 / 数据增强需要 / 其他)
  - 用户可从下拉选预设, 也可自由输入
  - 入参/出参: v-model:modelValue 双向绑定
  - 零业务耦合: 仅产出 comment 字符串, 不参与 API 调用
-->
<template>
  <div class="correction-comment">
    <el-select
      v-model="selectedPreset"
      placeholder="选择常见修正原因 (可选)"
      size="small"
      clearable
      filterable
      allow-create
      style="width: 100%;"
      @change="onPresetChange"
    >
      <el-option
        v-for="opt in PRESET_OPTIONS"
        :key="opt"
        :label="opt"
        :value="opt"
      />
    </el-select>
    <el-input
      v-model="customText"
      type="textarea"
      :rows="2"
      :maxlength="500"
      show-word-limit
      placeholder="补充说明 (选填, 最多 500 字)"
      style="margin-top: 6px;"
      @input="onCustomInput"
    />
  </div>
</template>

<script setup lang="ts">
/**
 * CorrectionCommentInput - 业务组件
 * 提供预设原因 + 自由输入, 选填
 */
import { ref, watch } from 'vue'

const PRESET_OPTIONS = [
  'AI 误判',
  '类别模糊',
  '遮挡',
  '多目标',
  '数据增强需要',
  '其他',
]

interface Props {
  modelValue?: string
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: '',
})

const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
}>()

// 内部状态: 选中的预设 (或用户自定义) + 自由输入
const selectedPreset = ref<string>('')
const customText = ref<string>('')

// 初始化: 如果外部有 modelValue, 解析它
watch(() => props.modelValue, (v) => {
  if (!v) {
    selectedPreset.value = ''
    customText.value = ''
  } else {
    // 简化: 整个值显示在 customText, 预设不强制匹配
    customText.value = v
    if ((PRESET_OPTIONS as readonly string[]).includes(v)) {
      selectedPreset.value = v
    } else {
      selectedPreset.value = ''
    }
  }
}, { immediate: true })

function onPresetChange(v: string | null) {
  if (!v) {
    // 清空预设
    emitChange()
    return
  }
  selectedPreset.value = v
  // 选预设时, 自动填入 customText
  customText.value = v
  emitChange()
}

function onCustomInput(v: string) {
  customText.value = v
  emitChange()
}

function emitChange() {
  // 优先级: customText > selectedPreset
  const final = customText.value || selectedPreset.value || ''
  emit('update:modelValue', final)
}
</script>

<style scoped>
.correction-comment {
  width: 100%;
}
</style>
