import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath} from 'node:url';

// بناء شاشة الإدارة بس (admin.html) — مستقل عن شاشة الكاشير الحالية عشان ما نكسرهاش
export default defineConfig({
  base: '/react/',
  plugins: [react()],
  build: {
    outDir: 'dist-admin',
    emptyOutDir: true,
    rollupOptions: {input: fileURLToPath(new URL('./admin.html', import.meta.url))},
  },
});
