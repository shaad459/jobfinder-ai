import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Talks to api_server.py (FastAPI) at http://localhost:8000 by default - see src/api.js.
// Runs on port 5173 (Vite's default), which is why api_server.py's CORS allowlist includes it.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
});
