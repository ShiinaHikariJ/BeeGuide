<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref } from "vue";
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  CloudSun,
  Hexagon,
  Leaf,
  LoaderCircle,
  LogOut,
  MapPin,
  MessageSquare,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Sprout,
  Activity,
  X,
} from "@lucide/vue";
import { api, stream } from "./api";
import { useWorkspace } from "./store";

import ProfileForm from "./components/ProfileForm.vue";
import type { KeeperProfile } from "./types";

const store = useWorkspace();
const tab = ref("chat"),
  sessionId = ref(""),
  messages = ref<any[]>([]),
  input = ref(""),
  busy = ref(false),
  status = ref(""),
  pending = ref<any>(null),
  error = ref(""),
  toast = ref(""),
  expandedRun = ref(""),
  selectedSource = ref<any>(null),
  taskDialog = ref<any>(null),
  feedback = ref(""),
  loginBusy = ref(false);
const email = ref("demo@beeguide.local"),
  password = ref("bee2026"),
  chatEnd = ref<HTMLElement>();
const profile = reactive<KeeperProfile>({
  city: "",
  stage: "",
  species: "",
  experience: "",
  equipment: "",
  hive_count: 0,
});
const showProfile = ref(false);
const profileModal = ref(false);
const today = new Intl.DateTimeFormat("zh-CN", {
  month: "long",
  day: "numeric",
  timeZone: "Asia/Shanghai",
}).format(new Date());
const pendingTasks = computed(() =>
  store.plans.flatMap((p) => p.tasks).filter((t) => t.status !== "done"),
);
const completedCount = computed(
  () =>
    store.plans.flatMap((p) => p.tasks).filter((t) => t.status === "done")
      .length,
);
const latestAdvice = computed(
  () =>
    [...messages.value].reverse().find((m) => m.payload?.citations)?.payload,
);
const title = computed(
  () =>
    ({
      chat: "养蜂助理",
      plans: "养护计划",
      knowledge: "知识资料",
      runs: "运行记录",
    })[tab.value] || "养蜂助理",
);
const greetings = [
  {
    icon: Sprout,
    title: "从零开始养蜂",
    detail: "了解准备事项，制定入门计划",
    prompt: "我完全不会养蜂，想先养一箱，需要准备什么？",
    tag: "入门准备",
  },
  {
    icon: CloudSun,
    title: "聊聊日常养护",
    detail: "结合天气与记录，梳理下一步",
    prompt: "我已有蜂群，明天适合检查蜂箱吗？",
    tag: "日常咨询",
  },
  {
    icon: ClipboardList,
    title: "继续上次计划",
    detail: "记录执行情况，安排后续复查",
    prompt: "我想继续上次的养护计划，请帮我看看还有什么需要完成。",
    tag: "持续跟进",
  },
];
function notify(text: string) {
  toast.value = text;
  window.setTimeout(() => (toast.value = ""), 3500);
}
function syncProfile() {
  Object.assign(
    profile,
    {
      city: "",
      stage: "",
      species: "",
      experience: "",
      equipment: "",
      hive_count: 0,
    },
    store.user?.profile || {},
  );
}
async function scrollChat() {
  await nextTick();
  chatEnd.value?.scrollIntoView({ behavior: "smooth", block: "end" });
}
async function login() {
  loginBusy.value = true;
  error.value = "";
  try {
    await store.login(email.value, password.value);
    syncProfile();
    const stored = localStorage.getItem("beeguide-session-" + store.user.id);
    if (stored) await loadSession(stored).catch(() => {});
  } catch (e: any) {
    error.value = e.message;
  } finally {
    loginBusy.value = false;
  }
}
async function loadSession(id: string) {
  if (busy.value) return;
  const data = await api("/sessions/" + id);
  sessionId.value = id;
  messages.value = data.messages;
  pending.value = data.awaiting;
  tab.value = "chat";
  localStorage.setItem("beeguide-session-" + store.user.id, id);
  await scrollChat();
}
async function newChat() {
  if (busy.value) return;
  sessionId.value = "";
  messages.value = [];
  pending.value = null;
  input.value = "";
  tab.value = "chat";
  localStorage.removeItem("beeguide-session-" + store.user.id);
}
function receive(event: any, assistant?: any) {
  if (event.type === "status") status.value = event.label;
  if (event.type === "text_delta" && assistant) {
    assistant.content += event.delta;
    scrollChat();
  }
  if (event.type === "advice" && assistant) {
    assistant.content = event.advice.answer;
    assistant.payload = event.advice;
    scrollChat();
  }
  if (event.type === "awaiting") pending.value = event;
  if (event.type === "plan_saved") {
    pending.value = null;
    notify("计划已保存，随时记录执行反馈");
  }
  if (event.type === "error") error.value = event.message;
}
async function send(text = input.value) {
  if (!text.trim() || busy.value) return;
  error.value = "";
  input.value = "";
  tab.value = "chat";
  busy.value = true;
  status.value = "开始处理咨询";
  try {
    if (!sessionId.value) {
      const session = await api("/sessions", { method: "POST" });
      sessionId.value = session.id;
      localStorage.setItem("beeguide-session-" + store.user.id, session.id);
    }
    messages.value.push({ role: "user", content: text, payload: {} });
    messages.value.push({ role: "assistant", content: "", payload: {} });
    const assistant = messages.value[messages.value.length - 1];
    await scrollChat();
    await stream(
      "/sessions/" + sessionId.value + "/messages",
      { content: text },
      (e) => receive(e, assistant),
    );
    await store.refresh();
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
    status.value = "";
    if (sessionId.value) await loadSession(sessionId.value).catch(() => {});
  }
}
async function savePlan(draftId: string, action = "save") {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await stream(
      "/sessions/" + sessionId.value + "/resume",
      { action, draft_id: draftId },
      receive,
    );
    await store.refresh();
  } catch (e: any) {
    error.value = e.message;
  } finally {
    busy.value = false;
    status.value = "";
    await loadSession(sessionId.value).catch(() => {});
  }
}
async function saveProfile() {
  try {
    store.user.profile = await api("/profile", {
      method: "PUT",
      body: JSON.stringify(profile),
    });
    showProfile.value = false;
    profileModal.value = false;
    notify("养蜂档案已更新");
  } catch (e: any) {
    error.value = e.message;
  }
}
async function scheduleTask(taskId: string, event: Event) {
  const value = (event.target as HTMLInputElement).value;
  try {
    await api("/tasks/" + taskId, {
      method: "PUT",
      body: JSON.stringify({ due_date: value || null }),
    });
    await store.refresh();
    notify("任务日期已更新");
  } catch (e: any) {
    error.value = e.message;
  }
}
async function saveFeedback() {
  if (!feedback.value.trim()) return;
  try {
    await api("/tasks/" + taskDialog.value.id + "/checkins", {
      method: "POST",
      body: JSON.stringify({ report: feedback.value, status: "done" }),
    });
    taskDialog.value = null;
    feedback.value = "";
    await store.refresh();
    notify("执行反馈已保存");
  } catch (e: any) {
    error.value = e.message;
  }
}
function logout() {
  if (busy.value) return;
  store.logout();
  messages.value = [];
  sessionId.value = "";
  tab.value = "chat";
  pending.value = null;
}
function onKey(event: KeyboardEvent) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
}
function modeLabel(mode: string) {
  return (
    (
      {
        live_model: "模型生成",
        local_evidence: "资料演示",
        model_fallback: "模型回退",
        review_fallback: "审查后资料整理",
        insufficient_evidence: "证据不足",
        pending: "等待补充",
      } as Record<string, string>
    )[mode] || mode
  );
}
function roleLabel(role: string) {
  return ({ coordinator: "协调员", advisor: "养蜂顾问", reviewer: "审查员" } as Record<string, string>)[role] || role;
}
function reviewLabel(status: string) {
  return ({ passed: "建议检查完成", blocked: "仅展示参考资料", unavailable: "检查暂不可用", not_required: "直接回答", pending: "检查中", revise: "正在修改" } as Record<string, string>)[status] || status;
}
onMounted(async () => {
  try {
    await store.restore();
    if (store.user) {
      syncProfile();
      const stored = localStorage.getItem("beeguide-session-" + store.user.id);
      if (stored) await loadSession(stored).catch(() => {});
    }
  } catch {
    store.logout();
  }
});
</script>

<template>
  <div v-if="!store.user" class="login-page">
    <div class="login-art">
      <div class="brand">
        <span class="logo"><Hexagon :size="32" /><span>b</span></span
        ><b>蜂伴</b><span>BeeGuide</span>
      </div>
      <div class="login-story">
        <div class="eyebrow">YOUR BEEKEEPING COMPANION</div>
        <h1>每一步养蜂，<br />都有可靠的陪伴。</h1>
        <p>
          从第一个问题，到第一次准备。<br />把知识变成清晰的行动，慢慢建立自己的养蜂经验。
        </p>
        <div class="large-hive">
          <span /><span /><span /><span /><i />
          <div class="bee bee-one">✦</div>
          <div class="bee bee-two">✦</div>
        </div>
        <div class="story-note">
          <Leaf :size="18" /> 有依据的建议 · 可继续的计划
        </div>
      </div>
    </div>
    <form class="login-form" @submit.prevent="login">
      <div class="small-label">欢迎来到蜂伴</div>
      <h2>开始你的养蜂旅程</h2>
      <p>进入演示空间，体验咨询、计划和持续跟进。</p>
      <label
        >账号<input
          v-model="email"
          type="email"
          autocomplete="username"
          required /></label
      ><label
        >密码<input
          v-model="password"
          type="password"
          autocomplete="current-password"
          required /></label
      ><button class="primary full" :disabled="loginBusy">
        <LoaderCircle v-if="loginBusy" class="spin" :size="18" /><span
          >进入演示空间</span
        ><ArrowRight :size="18" />
      </button>
      <p class="demo-note">
        演示账号已填入。第二个账号：second@beeguide.local<br />两个账号密码均为
        bee2026，档案与计划分别保存。
      </p>
      <div v-if="error" class="error">{{ error }}</div>
    </form>
  </div>
  <div v-else class="shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="logo"><Hexagon :size="32" /><span>b</span></span
        ><b>蜂伴</b><span>BeeGuide</span>
      </div>
      <button class="new-chat" :disabled="busy" @click="newChat">
        <Plus :size="18" /> 新的养蜂咨询
      </button>
      <div class="nav-label">工作空间</div>
      <nav>
        <button :class="{ active: tab === 'chat' }" @click="tab = 'chat'">
          <MessageSquare :size="18" />养蜂助理<span class="nav-dot" /></button
        ><button :class="{ active: tab === 'plans' }" @click="tab = 'plans'">
          <ClipboardList :size="18" />养护计划<span
            v-if="pendingTasks.length"
            class="count"
            >{{ pendingTasks.length }}</span
          ></button
        ><button
          :class="{ active: tab === 'knowledge' }"
          @click="tab = 'knowledge'"
        >
          <BookOpen :size="18" />知识资料</button
        ><button :class="{ active: tab === 'runs' }" @click="tab = 'runs'">
          <Activity :size="18" />运行记录
        </button>
      </nav>
      <div class="nav-label recent-label">最近咨询</div>
      <div class="recent">
        <button
          v-for="session in store.sessions.slice(0, 8)"
          :key="session.id"
          :class="{ selected: session.id === sessionId }"
          :disabled="busy"
          @click="loadSession(session.id)"
        >
          <MessageSquare :size="14" /><span>{{ session.title }}</span>
        </button>
        <p v-if="!store.sessions.length">你的咨询会保存在这里</p>
      </div>
      <div class="sidebar-note">
        <Sprout :size="20" /><b>从了解开始，循序渐进</b>
        <p>记录每次观察，让经验慢慢积累。</p>
      </div>
      <div class="user-bar">
        <div class="avatar">蜂</div>
        <div>
          <b>{{ store.user.name }}</b
          ><small>个人养蜂空间</small>
        </div>
        <button title="退出登录" :disabled="busy" @click="logout">
          <LogOut :size="17" />
        </button>
      </div>
    </aside>
    <div class="main">
      <header>
        <div>
          <span class="breadcrumb">我的工作空间</span
          ><ChevronRight :size="14" /><b>{{ title }}</b>
        </div>
        <div class="header-right">
          <button
            class="header-control"
            aria-label="打开养蜂档案"
            @click="
              profileModal = true;
              showProfile = false;
            "
          >
            <Settings2 :size="17" /></button
          ><button
            class="header-control mobile-logout"
            aria-label="退出演示账号"
            :disabled="busy"
            @click="logout"
          >
            <LogOut :size="16" />
          </button>
          <span class="mode-badge"
            ><i />{{
              store.health.model_mode === "live_model"
                ? "已配置模型服务"
                : "本地资料演示"
            }}</span
          ><span class="date">{{ today }}</span>
        </div>
      </header>
      <div v-if="error" class="error page-error">
        {{ error }}<button @click="error = ''"><X :size="16" /></button>
      </div>
      <main class="workspace" :class="{ 'wide-workspace': tab !== 'chat' }">
        <section v-if="tab === 'chat'" class="conversation">
          <div v-if="!messages.length" class="welcome">
            <div class="welcome-top">
              <div>
                <div class="eyebrow"><span /> 一起，把养蜂这件事做好</div>
                <h1>你好，今天想聊点什么？</h1>
                <p>
                  说说你的情况，我会帮你找资料、理思路，<br
                    class="desktop-break"
                  />把下一步整理成可以执行的计划。
                </p>
              </div>
              <div class="hive-art">
                <div class="sun" />
                <span /><span /><span /><i />
                <div class="leaf one" />
                <div class="leaf two" />
                <b>✦</b>
              </div>
            </div>
            <div class="quick-cards">
              <button
                v-for="item in greetings"
                :key="item.title"
                :disabled="busy"
                @click="send(item.prompt)"
              >
                <span class="quick-icon"
                  ><component :is="item.icon" :size="23" /></span
                ><small>{{ item.tag }}</small>
                <h3>{{ item.title }}</h3>
                <p>{{ item.detail }}</p>
                <ArrowRight class="quick-arrow" :size="17" />
              </button>
            </div>
            <div class="trust-row">
              <ShieldCheck :size="16" /><span
                >关键建议附上来源，信息不足时先补充情况。</span
              >
            </div>
            <div class="starter">
              <div><Leaf :size="19" /><b>刚开始养蜂？先准备，再出发。</b></div>
              <p>从防护装备、场地和当地蜜源开始了解，给自己一个从容的起点。</p>
              <button @click="send('新手养蜂前应该准备哪些防护装备？')">
                看看准备事项 <ArrowRight :size="15" />
              </button>
            </div>
          </div>
          <div v-else class="messages">
            <div class="chat-heading">
              <span><Sparkles :size="17" /> 本次咨询</span
              ><small>记录会自动保存</small>
            </div>
            <article
              v-for="(message, index) in messages"
              :key="message.id || index"
              class="message"
              :class="message.role"
            >
              <div class="message-avatar" :class="message.role">
                <Hexagon v-if="message.role === 'assistant'" :size="20" /><span
                  v-else
                  >我</span
                >
              </div>
              <div class="message-body">
                <div class="message-name">
                  {{ message.role === "assistant" ? "蜂伴" : "你"
                  }}<span v-if="message.payload?.mode">{{
                    modeLabel(message.payload.mode)
                  }}</span>
                </div>
                <div class="message-content">
                  {{ message.content
                  }}<span
                    v-if="
                      busy && index === messages.length - 1 && !message.content
                    "
                    class="thinking"
                    ><i /><i /><i
                  /></span>
                </div>
                <div v-if="message.payload?.warning" class="inline-note">
                  {{ message.payload.warning }}
                </div>
                <div v-if="message.payload?.review?.status && message.payload.review.status !== 'not_required'" class="advice-review">
                  <ShieldCheck :size="16" />
                  <div>
                    <b>{{ reviewLabel(message.payload.review.status) }}</b>
                    <span v-if="message.payload.review.revision_count"> · 已修改 {{ message.payload.review.revision_count }} 次</span>
                    <p>{{ message.payload.review.summary }}</p>
                    <small>自动检查用于辅助核对，具体养护仍需结合当地情况。</small>
                  </div>
                </div>
                <div
                  v-if="message.payload?.conditions?.length"
                  class="conditions"
                >
                  <b>适用与待确认</b>
                  <p
                    v-for="condition in message.payload.conditions"
                    :key="condition"
                  >
                    {{ condition }}
                  </p>
                </div>
                <div v-if="message.payload?.known_facts?.length" class="facts">
                  <span v-for="fact in message.payload.known_facts" :key="fact"
                    ><Check :size="12" />{{ fact }}</span
                  >
                </div>
                <div v-if="message.payload?.weather" class="weather-result">
                  <CloudSun :size="20" />
                  <div v-if="message.payload.weather.available">
                    <b>{{ message.payload.weather.city }} · 近期预报</b>
                    <div class="weather-days">
                      <span
                        v-for="day in message.payload.weather.days"
                        :key="day.date"
                        >{{ day.date.slice(5)
                        }}<strong
                          >{{ day.temperature_min }}–{{
                            day.temperature_max
                          }}°C</strong
                        ><small
                          >降水概率 {{ day.rain_probability }}% · 风
                          {{ day.wind_max }} km/h</small
                        ></span
                      >
                    </div>
                    <small>Open-Meteo · 预报仅作环境参考</small>
                  </div>
                  <p v-else>{{ message.payload.weather.reason }}</p>
                </div>
                <div v-if="message.payload?.actions?.length" class="draft-card">
                  <div class="draft-head">
                    <ClipboardList :size="17" /><b>{{
                      message.payload.title
                    }}</b
                    ><span>任务草稿</span>
                  </div>
                  <ol>
                    <li
                      v-for="action in message.payload.actions"
                      :key="action.title"
                    >
                      <b>{{ action.title }}</b>
                      <p>{{ action.detail }}</p>
                    </li>
                  </ol>
                  <div
                    v-if="
                      pending?.kind === 'confirm' &&
                      pending.draft_id === message.payload.draft_id
                    "
                    class="draft-footer"
                  >
                    <span>确认后保存到你的养护计划</span
                    ><button
                      class="primary"
                      :disabled="busy"
                      @click="savePlan(message.payload.draft_id)"
                    >
                      <Plus :size="15" />保存计划
                    </button>
                  </div>
                  <div v-else class="draft-footer">
                    <span
                      >这份草稿已结束；保存后的任务可在养护计划中查看。</span
                    >
                  </div>
                </div>
                <div
                  v-if="message.payload?.citations?.length"
                  class="citations"
                >
                  <div class="citation-label">
                    <BookOpen :size="14" /> 本次参考资料
                  </div>
                  <button
                    v-for="citation in message.payload.citations"
                    :key="citation.id"
                    @click="selectedSource = citation"
                  >
                    <span>{{ citation.url ? "资料" : "整理" }}</span
                    >{{ citation.title }}<ChevronRight :size="13" />
                  </button>
                </div>
              </div>
            </article>
            <div ref="chatEnd" />
          </div>
          <div class="composer-area">
            <div v-if="busy" class="live-status">
              <LoaderCircle class="spin" :size="14" />{{ status || "正在处理" }}
            </div>
            <div v-else-if="pending?.kind === 'clarify'" class="live-status">
              <MessageSquare :size="14" />等待你补充情况，刷新后也可以继续
            </div>
            <form class="composer" @submit.prevent="send()">
              <textarea
                v-model="input"
                aria-label="咨询内容"
                placeholder="说说你遇到的问题，或者想完成的事情…"
                :disabled="busy"
                rows="2"
                @keydown="onKey"
              />
              <div class="composer-bottom">
                <span><Sparkles :size="14" />根据你的情况，梳理下一步</span
                ><button
                  class="send-button"
                  :disabled="busy || !input.trim()"
                  title="发送咨询"
                >
                  <LoaderCircle v-if="busy" class="spin" :size="19" /><ArrowUp
                    v-else
                    :size="20"
                  />
                </button>
              </div>
            </form>
            <small class="composer-note"
              >建议需结合本地蜂种和实际情况；重要操作请向当地专业人员核实。</small
            >
          </div>
        </section>
        <aside v-if="tab === 'chat'" class="context-panel">
          <section class="panel-card profile-card">
            <div class="panel-head">
              <h3>我的养蜂档案</h3>
              <button
                aria-label="编辑养蜂档案"
                @click="showProfile = !showProfile"
              >
                <Settings2 :size="17" />
              </button>
            </div>
            <div class="profile-summary">
              <div class="profile-icon"><Sprout :size="25" /></div>
              <b>{{
                store.user.profile.stage === "keeping"
                  ? "已有蜂群，持续学习"
                  : "从准备开始，慢慢积累"
              }}</b
              ><span
                ><MapPin :size="13" />{{
                  store.user.profile.city || "尚未填写城市"
                }}</span
              >
            </div>
            <div class="profile-lines">
              <p>
                <span>蜂种</span
                ><b>{{ store.user.profile.species || "尚未确认" }}</b>
              </p>
              <p>
                <span>蜂箱数量</span
                ><b>{{ store.user.profile.hive_count || "尚未记录" }}</b>
              </p>
            </div>
            <button class="secondary full" @click="showProfile = true">
              {{ store.user.profile.city ? "更新我的情况" : "完善养蜂档案"
              }}<ArrowRight :size="14" />
            </button>
            <ProfileForm
              v-if="showProfile"
              :profile="profile"
              @save="saveProfile"
            />
          </section>
          <section class="panel-card">
            <div class="panel-head">
              <h3>接下来要做</h3>
              <span class="count">{{ pendingTasks.length }}</span>
            </div>
            <div v-if="!pendingTasks.length" class="empty-tasks">
              <div><ClipboardList :size="24" /></div>
              <p>还没有待办任务</p>
              <small>咨询后保存计划，<br />下一步就会出现在这里。</small>
            </div>
            <button
              v-for="task in pendingTasks.slice(0, 3)"
              :key="task.id"
              class="mini-task"
              @click="tab = 'plans'"
            >
              <span class="task-circle" />
              <div>
                <b>{{ task.title }}</b
                ><small>{{ task.due_date || "按实际情况安排" }}</small>
              </div>
              <ChevronRight :size="14" /></button
            ><button
              v-if="pendingTasks.length"
              class="text-button"
              @click="tab = 'plans'"
            >
              查看全部计划 <ArrowRight :size="14" />
            </button>
          </section>
          <section class="gentle-note">
            <div><Leaf :size="17" /><b>观察，比着急操作更重要</b></div>
            <p>把日期、现场所见和执行情况记下来，下次咨询会更有依据。</p>
          </section>
          <div v-if="latestAdvice" class="source-summary">
            <ShieldCheck :size="15" /><span
              >本次建议参考 {{ latestAdvice.citations.length }} 条资料</span
            >
          </div>
        </aside>
        <section v-if="tab === 'plans'" class="full-page">
          <div class="page-title">
            <div class="eyebrow">SMALL STEPS, STEADY PROGRESS</div>
            <h1>把建议，变成下一步。</h1>
            <p>记录每一次执行，让后续养护更有依据。</p>
          </div>
          <div class="stats-row">
            <div>
              <span>已保存计划</span
              ><b>{{ store.plans.length }}<small>份</small></b>
            </div>
            <div>
              <span>等待执行</span
              ><b>{{ pendingTasks.length }}<small>项</small></b>
            </div>
            <div>
              <span>已记录完成</span
              ><b>{{ completedCount }}<small>项</small></b>
            </div>
          </div>
          <div v-if="!store.plans.length" class="empty-page">
            <ClipboardList :size="40" />
            <h3>你的第一个养护计划，从咨询开始</h3>
            <p>和蜂伴聊聊情况，确认任务后在这里持续跟进。</p>
            <button class="primary" @click="newChat">
              开始咨询 <ArrowRight :size="16" />
            </button>
          </div>
          <section
            v-for="plan in store.plans"
            :key="plan.id"
            class="saved-plan"
          >
            <div class="plan-title">
              <span class="quick-icon"><ClipboardList :size="20" /></span>
              <div>
                <h3>{{ plan.title }}</h3>
                <small
                  >{{ plan.created_at.slice(0, 10) }} ·
                  {{ plan.tasks.length }} 项任务</small
                >
              </div>
            </div>
            <div v-for="task in plan.tasks" :key="task.id" class="task-row">
              <span
                class="task-circle"
                :class="{ done: task.status === 'done' }"
                ><Check v-if="task.status === 'done'" :size="13"
              /></span>
              <div>
                <h4>{{ task.title }}</h4>
                <p>{{ task.detail }}</p>
                <label class="task-date"
                  >安排日期<input
                    type="date"
                    :aria-label="task.title + '的计划日期'"
                    :value="task.due_date || ''"
                    @change="scheduleTask(task.id, $event)" /></label
                ><small v-if="task.reports.length" class="report-note"
                  >最近反馈：{{ task.reports[0].report }}</small
                >
              </div>
              <button
                class="secondary"
                @click="
                  taskDialog = task;
                  feedback = '';
                "
              >
                {{ task.status === "done" ? "补充反馈" : "记录执行" }}
              </button>
            </div>
          </section>
        </section>
        <section v-if="tab === 'knowledge'" class="full-page">
          <div class="page-title">
            <div class="eyebrow">KNOWLEDGE WITH SOURCES</div>
            <h1>每条建议，都有来处。</h1>
            <p>基础资料的中文整理，保留来源、适用地区和核对状态。</p>
          </div>
          <div class="library-note">
            <ShieldCheck :size="20" />
            <p>
              当前有 {{ store.knowledge.length }} 条资料，覆盖入门准备、装备、选址、季节养护、食物、农药风险和咨询记录。请核对地区、蜂种与季节条件；中文整理尚待专业复核。
            </p>
          </div>
          <div class="knowledge-grid">
            <button
              v-for="item in store.knowledge"
              :key="item.id"
              class="knowledge-card"
              @click="selectedSource = item"
            >
              <span class="knowledge-type">{{
                item.url ? "机构资料 · 中文整理" : "项目流程说明"
              }}</span
              ><BookOpen :size="23" />
              <h3>{{ item.title }}</h3>
              <p>{{ item.content }}</p>
              <div>
                <span>{{ item.source }}</span
                ><ArrowRight :size="16" />
              </div>
            </button>
          </div>
        </section>
        <section v-if="tab === 'runs'" class="full-page">
          <div class="page-title">
            <div class="eyebrow">OBSERVE THE WORKFLOW</div>
            <h1>看见每次咨询的过程。</h1>
            <p>查看真实工具调用、资料来源和耗时，了解建议如何形成。</p>
          </div>
          <div class="library-note">
            <Activity :size="20" />
            <p>
              {{
                store.health.model_mode === "live_model"
                  ? "当前已配置真实模型。调用失败时会明确标记回退。"
                  : "当前为本地资料演示：规则路由 + BM25 / TF-IDF 检索 + 资料整理，未调用大模型或语义 Embedding。"
              }}
              运行记录展示执行摘要。
            </p>
          </div>
          <div v-if="!store.runs.length" class="empty-page">
            <Activity :size="40" />
            <h3>完成一次咨询，就能查看流程</h3>
            <p>运行状态、工具结果和耗时会出现在这里。</p>
          </div>
          <article v-for="run in store.runs" :key="run.id" class="run-card">
            <button
              class="run-header"
              @click="expandedRun = expandedRun === run.id ? '' : run.id"
            >
              <span class="run-icon"><Activity :size="19" /></span>
              <div>
                <b>{{ modeLabel(run.mode) }}</b
                ><small
                  >{{ run.created_at.slice(0, 19).replace("T", " ") }} ·
                  {{
                    run.status === "complete"
                      ? "已完成"
                      : run.status === "waiting_clarify"
                        ? "等待补充"
                        : run.status === "waiting_confirm"
                          ? "等待保存确认"
                          : run.status
                  }}</small
                >
              </div>
              <span class="duration"
                >{{ (run.metrics.duration_ms / 1000).toFixed(2) }} s</span
              ><ChevronDown :size="17" />
            </button>
            <div v-if="expandedRun === run.id" class="run-details">
              <div class="metrics">
                <span
                  >真实首 token：<b>{{
                    run.metrics.first_token_ms != null
                      ? run.metrics.first_token_ms + " ms"
                      : "无模型 token"
                  }}</b></span
                ><span
                  >工具记录：<b>{{ run.trace.length }}</b></span
                ><span v-if="run.metrics.usage.total_tokens"
                  >Token：<b>{{ run.metrics.usage.total_tokens }}</b></span
                >
                <span v-if="run.metrics.architecture">流程：<b>{{ run.metrics.architecture === 'local' ? '本地资料演示' : run.metrics.architecture === 'multi' ? (run.metrics.workflow_path === 'direct' ? '简单问答' : '多角色协作') : '固定流程' }}</b></span>
                <span v-if="run.metrics.coordination?.rounds">协调：<b>{{ run.metrics.coordination.rounds }} 轮 · {{ run.metrics.coordination.tool_attempts }} 次工具请求</b></span>
                <span v-if="run.metrics.review_status && run.metrics.review_status !== 'not_required'">审查：<b>{{ reviewLabel(run.metrics.review_status) }}</b></span>
              </div>
              <div v-for="(trace, i) in run.trace" :key="i" class="trace-row">
                <span>{{ i + 1 }}</span>
                <div>
                  <b>{{ trace.tool }}</b>
                  <small v-if="trace.agent">{{ roleLabel(trace.agent) }}<template v-if="trace.round"> · 第 {{ trace.round }} 轮</template><template v-if="trace.fallback"> · 系统补足</template><template v-if="trace.usage?.total_tokens"> · {{ trace.usage.total_tokens }} tokens</template></small>
                  <p>{{ trace.summary }}</p>
                  <small v-if="trace.retrieval_mode">{{
                    trace.retrieval_mode
                  }}</small
                  ><small v-if="trace.warning">{{ trace.warning }}</small>
                </div>
                <small>{{ trace.duration_ms || 0 }} ms</small>
              </div>
            </div>
          </article>
        </section>
      </main>
    </div>
    <div
      v-if="profileModal"
      class="modal-backdrop"
      @click.self="profileModal = false"
    >
      <section class="modal">
        <button
          class="modal-close"
          aria-label="关闭养蜂档案"
          @click="profileModal = false"
        >
          <X :size="21" />
        </button>
        <div class="eyebrow">我的养蜂档案</div>
        <h2>让建议更适合你的情况</h2>
        <p>只记录你确认的信息，尚未了解的可以留空。</p>
        <ProfileForm :profile="profile" @save="saveProfile" />
      </section>
    </div>
    <div v-if="toast" class="toast"><CheckCircle2 :size="18" />{{ toast }}</div>
    <div
      v-if="selectedSource"
      class="modal-backdrop"
      @click.self="selectedSource = null"
    >
      <section class="modal">
        <button
          class="modal-close"
          aria-label="关闭资料"
          @click="selectedSource = null"
        >
          <X :size="21" />
        </button>
        <div class="eyebrow">参考资料</div>
        <h2>{{ selectedSource.title }}</h2>
        <p class="source-content">{{ selectedSource.content }}</p>
        <dl>
          <dt>来源</dt>
          <dd>{{ selectedSource.source }}</dd>
          <template v-if="selectedSource.source_title">
            <dt>原文标题</dt>
            <dd>{{ selectedSource.source_title }}</dd>
          </template>
          <dt>原文小节</dt>
          <dd>{{ selectedSource.section }}</dd>
          <dt>适用地域</dt>
          <dd>{{ selectedSource.region }}</dd>
          <template v-if="selectedSource.supported_species?.length">
            <dt>适用蜂种</dt>
            <dd>{{ selectedSource.supported_species.join('、') }}</dd>
          </template>
          <template v-if="selectedSource.seasons?.length">
            <dt>季节条件</dt>
            <dd>{{ selectedSource.seasons.join('、') }}</dd>
          </template>
          <template v-if="selectedSource.applicability">
            <dt>使用条件</dt>
            <dd>{{ selectedSource.applicability }}</dd>
          </template>
          <template v-if="selectedSource.published_at">
            <dt>原文发布日期</dt>
            <dd>{{ selectedSource.published_at }}</dd>
          </template>
          <template v-if="selectedSource.checked_at">
            <dt>原文核对日期</dt>
            <dd>{{ selectedSource.checked_at }}</dd>
          </template>
          <dt>核对状态</dt>
          <dd>{{ selectedSource.review_status }}</dd>
          <dt>整理版本</dt>
          <dd>{{ selectedSource.version }}</dd>
        </dl>
        <a
          v-if="selectedSource.url"
          class="primary"
          :href="selectedSource.url"
          target="_blank"
          rel="noopener noreferrer"
          >查看原始资料 <ArrowRight :size="16"
        /></a>
      </section>
    </div>
    <div
      v-if="taskDialog"
      class="modal-backdrop"
      @click.self="taskDialog = null"
    >
      <form class="modal" @submit.prevent="saveFeedback">
        <button
          type="button"
          class="modal-close"
          aria-label="关闭反馈"
          @click="taskDialog = null"
        >
          <X :size="21" />
        </button>
        <div class="eyebrow">执行反馈</div>
        <h2>{{ taskDialog.title }}</h2>
        <p>记录你实际做了什么、何时完成和观察到的情况。</p>
        <textarea
          v-model="feedback"
          aria-label="执行反馈内容"
          rows="5"
          placeholder="例如：今天已核对面罩和手套，蜂箱规格还需要向当地蜂友确认。"
          maxlength="2000"
          required
        /><button class="primary full">
          确认记录完成 <Check :size="16" />
        </button>
      </form>
    </div>
  </div>
</template>
