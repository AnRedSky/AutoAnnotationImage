<script setup lang="ts">
/**
 * 邀请成员弹窗 (v3.3.1)
 * ======================
 *
 * Props:
 *  - modelValue: boolean
 *  - teamId:     number
 *  - allUsers:   { id, username, role }[]   来自父组件的可用用户列表
 *  - memberIds:  Set<number>                已在团队中的用户 ID (用于过滤)
 *
 * Emits:
 *  - update:modelValue
 *  - invited
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { teamApi } from '@/api'

const props = defineProps<{
  modelValue: boolean
  teamId: number
  allUsers: { id: number; username: string; role: string }[]
  memberIds: Set<number>
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'invited'): void
}>()

const inviteUserId = ref<number | undefined>()
const inviteRole = ref('editor')
const submitting = ref(false)

const teamRoles = [
  { label: '可管理', value: 'manager', desc: '管理成员 + 配置数据集权限 + 编辑标注' },
  { label: '可编辑', value: 'editor', desc: '可对共享数据集进行标注' },
  { label: '仅阅读', value: 'viewer', desc: '只读' },
]

/** 过滤掉已在团队中的用户 */
const availableUsers = computed(() =>
  props.allUsers.filter((u) => !props.memberIds.has(u.id))
)

const close = () => emit('update:modelValue', false)

const onSubmit = async () => {
  if (!inviteUserId.value) {
    ElMessage.warning('请选择用户')
    return
  }
  submitting.value = true
  try {
    await teamApi.inviteMember(props.teamId, {
      user_id: inviteUserId.value,
      role: inviteRole.value,
    })
    ElMessage.success('邀请成功')
    inviteUserId.value = undefined
    inviteRole.value = 'editor'
    emit('invited')
    close()
  } catch (e: any) {
    ElMessage.error('邀请失败: ' + (e?.response?.data?.detail || e?.message))
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="邀请成员加入团队"
    width="440px"
    @close="close"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <el-form label-position="top">
      <el-form-item label="选择用户">
        <el-select
          v-model="inviteUserId"
          filterable
          placeholder="搜索用户名"
          style="width: 100%"
        >
          <el-option
            v-for="u in availableUsers"
            :key="u.id"
            :label="`${u.username} (${u.role})`"
            :value="u.id"
          />
        </el-select>
        <div v-if="!availableUsers.length" class="form-hint">
          没有可邀请的用户(可能所有用户都已在团队中,或当前用户无权查看用户列表)
        </div>
      </el-form-item>
      <el-form-item label="角色">
        <el-radio-group v-model="inviteRole" class="role-radio-group">
          <el-radio v-for="r in teamRoles" :key="r.value" :value="r.value" class="role-radio">
            <span class="role-name">{{ r.label }}</span>
            <span class="role-desc">{{ r.desc }}</span>
          </el-radio>
        </el-radio-group>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="onSubmit">确认邀请</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.form-hint {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 4px;
}
.role-radio-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.role-radio {
  display: flex;
  align-items: center;
}
.role-name {
  font-weight: 500;
  margin-right: 8px;
}
.role-desc {
  font-size: 12px;
  color: var(--text-secondary);
}
</style>
