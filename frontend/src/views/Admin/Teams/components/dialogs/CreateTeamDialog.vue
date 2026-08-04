<script setup lang="ts">
/**
 * 创建团队弹窗 (v3.3.1)
 * ======================
 *
 * Props:
 *  - modelValue: boolean   v-model 绑定 (显示/隐藏)
 *
 * Emits:
 *  - update:modelValue     关闭弹窗
 *  - created               创建成功后回调 (通知父组件刷新列表)
 */
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { teamApi } from '@/api'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'created'): void
}>()

const form = ref({ name: '', slug: '', description: '', max_members: 20 })
let slugManuallyEdited = false
const submitting = ref(false)

/** 从名称生成 URL 友好的 slug */
const generateSlug = (name: string): string =>
  name
    .toLowerCase()
    .trim()
    .replace(/[^\w\u4e00-\u9fa5\s-]/g, '')
    .replace(/[\s_]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 50)

watch(
  () => form.value.name,
  (newName) => {
    if (!slugManuallyEdited) {
      form.value.slug = generateSlug(newName)
    }
  }
)

/** 重置表单 */
const reset = () => {
  form.value = { name: '', slug: '', description: '', max_members: 20 }
  slugManuallyEdited = false
}

/** 关闭弹窗 */
const close = () => {
  emit('update:modelValue', false)
  reset()
}

/** 提交创建 */
const onSubmit = async () => {
  if (!form.value.name) {
    ElMessage.warning('团队名称不能为空')
    return
  }
  submitting.value = true
  try {
    await teamApi.create({ ...form.value })
    ElMessage.success('团队创建成功,您自动成为队长')
    emit('created')
    close()
  } catch (e: any) {
    ElMessage.error('创建失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="创建团队"
    width="460px"
    @close="close"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <el-form label-position="top" :model="form">
      <el-form-item label="名称">
        <el-input v-model="form.name" placeholder="如: 标注组A" />
      </el-form-item>
      <el-form-item label="短标识 (slug)">
        <el-input
          v-model="form.slug"
          placeholder="自动从名称生成,可手动修改"
          @input="slugManuallyEdited = true"
        />
      </el-form-item>
      <el-form-item label="描述">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="2"
          placeholder="团队描述(可选)"
        />
      </el-form-item>
      <el-form-item label="成员上限">
        <el-input-number v-model="form.max_members" :min="2" :max="100" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="onSubmit">创建</el-button>
    </template>
  </el-dialog>
</template>
