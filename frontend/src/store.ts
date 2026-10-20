import { defineStore } from "pinia";
import { api, auth } from "./api";
export const useWorkspace = defineStore("workspace", {
  state: () => ({
    user: null as any,
    health: {} as any,
    sessions: [] as any[],
    plans: [] as any[],
    runs: [] as any[],
    knowledge: [] as any[],
  }),
  actions: {
    async login(email: string, password: string) {
      const data = await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      auth.token = data.token;
      localStorage.setItem("beeguide-token", data.token);
      this.user = data.user;
      await this.refresh();
    },
    async refresh() {
      const [health, sessions, plans, runs, knowledge] = await Promise.all([
        api("/health"),
        api("/sessions"),
        api("/plans"),
        api("/runs"),
        api("/knowledge"),
      ]);
      Object.assign(this, { health, sessions, plans, runs, knowledge });
    },
    async restore() {
      if (!auth.token) return;
      this.user = await api("/me");
      await this.refresh();
    },
    logout() {
      auth.token = "";
      localStorage.removeItem("beeguide-token");
      this.$reset();
    },
  },
});
