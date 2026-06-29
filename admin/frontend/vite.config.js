import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  publicDir: "../../scripts/chat2skill/admin_static",
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765"
    }
  },
  build: {
    outDir: "../../scripts/chat2skill/admin_static",
    emptyOutDir: false
  }
});
