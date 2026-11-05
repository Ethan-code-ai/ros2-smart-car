import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { ROBOT_API_PROXY_PATTERN } from "./src/robotProxy";
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const target = env.BUSINESS_PROXY_TARGET || "http://127.0.0.1:8000";
  const proxy = {
    "/api/control": { target: env.BUSINESS_PROXY_TARGET || "http://127.0.0.1:8000", changeOrigin: true, timeout: 65000 },
    "/api/v1": { target: env.BUSINESS_PROXY_TARGET || "http://127.0.0.1:8000", changeOrigin: true, timeout: 30000 },
    [ROBOT_API_PROXY_PATTERN]: { target, changeOrigin: true, timeout: 50000 },
    "/ws/status": { target, ws: true, changeOrigin: true },
    "/video_feed": { target, changeOrigin: true },
  };
  return { plugins: [react()], server: { host: "127.0.0.1", port: 5173, strictPort: true, proxy }, preview: { host: "127.0.0.1", port: 5173, strictPort: true, proxy } };
});
