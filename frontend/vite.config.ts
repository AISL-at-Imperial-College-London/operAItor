import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` proxies the Python backend (`acetone-plant serve`, default port 8001).
// Point it elsewhere with e.g. `$env:PLANT_PORT=9000; npm run dev`.
const port = process.env.PLANT_PORT ?? "8001";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/ws": { target: `ws://127.0.0.1:${port}`, ws: true },
      "/api": `http://127.0.0.1:${port}`,
    },
  },
});
