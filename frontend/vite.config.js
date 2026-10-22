import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev server mirrors the production nginx routes: /api -> control plane, /gateway-status -> gateway readiness.
const CP = process.env.VQ_CONTROL_PLANE ?? "http://localhost:8000";
const GW = process.env.VQ_GATEWAY ?? "http://localhost:8080";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": { target: CP, changeOrigin: false, rewrite: (p) => p.replace(/^\/api/, "") },
      "/gateway-status": { target: GW, rewrite: () => "/readyz" },
    },
  },
});
