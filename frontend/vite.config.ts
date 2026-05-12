import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // HTTP-эндпоинты FastAPI
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      // WebSocket-канал событий аналитики (без ws:true Vite не делает upgrade,
      // и фронт мгновенно ловит close → «WS отключён»).
      '/api/events': {
        target: 'ws://localhost:8000',
        ws: true,
        changeOrigin: true,
      },
    },
  },
});
