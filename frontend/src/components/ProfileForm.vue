<script setup lang="ts">
import type { KeeperProfile } from "../types";
defineProps<{ profile: KeeperProfile }>();
defineEmits<{ save: [] }>();
</script>

<template>
  <form class="profile-form" @submit.prevent="$emit('save')">
    <label
      >所在城市<input
        v-model="profile.city"
        placeholder="例如杭州"
        maxlength="80"
    /></label>
    <label
      >当前阶段<select v-model="profile.stage">
        <option value="">尚未填写</option>
        <option value="preparing">准备开始</option>
        <option value="keeping">已有蜂群</option>
      </select></label
    >
    <label
      >蜂种<select v-model="profile.species">
        <option value="">尚未确认</option>
        <option>中蜂</option>
        <option>意蜂</option>
        <option>其他蜂种</option>
      </select></label
    >
    <label
      >蜂箱数量<input
        v-model.number="profile.hive_count"
        type="number"
        min="0"
        max="10000"
    /></label>
    <label
      >养蜂经验<input
        v-model="profile.experience"
        placeholder="例如刚开始了解"
        maxlength="100"
    /></label>
    <label
      >已有设备<input
        v-model="profile.equipment"
        placeholder="例如面罩、蜂箱"
        maxlength="300"
    /></label>
    <button class="primary full">保存档案</button>
  </form>
</template>
