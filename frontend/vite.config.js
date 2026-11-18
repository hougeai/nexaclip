import { fileURLToPath, URL } from 'node:url'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'

import Unocss from 'unocss/vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const viteEnv = loadEnv(mode, './')
  // 本地开发代理：/api 转发到本地后端（端口取自 VITE_BACKEND_PORT，默认 3003），
  // 与生产「前端直连 Space 域名」同构——业务代码 baseURL 一致，无环境分支
  const backendPort = viteEnv.VITE_BACKEND_PORT || 3003
  return {
    base: viteEnv.VITE_BASE || '/',
    server: {
      host: '0.0.0.0',
      port: viteEnv.VITE_PORT || 5173,
      open: false,
      strictPort: true,
      proxy: {
        '/api': {
          target: `http://127.0.0.1:${backendPort}`,
          changeOrigin: true
        }
      }
    },
    build: {
      outDir: 'dist',
      assetsDir: 'assets',
      chunkSizeWarningLimit: 1500
    },
    plugins: [
      vue(),
      AutoImport({
        include: [/\.[jt]sx?$/, /\.vue$/, /\.vue\?vue/],
        imports: ['vue', 'vue-router', 'pinia', '@vueuse/core'],
        dirs: ['src/stores'], // store 的 use*Store 自动引入
        dts: false,
        // 生成 .eslintrc-auto-import.json：eslint 据此识别自动导入的全局变量（对齐参考项目）
        eslintrc: {
          enabled: true,
          filepath: './.eslintrc-auto-import.json',
          globalsPropValue: true
        }
      }),
      Components({
        dirs: ['src/components'],
        extensions: ['vue'],
        dts: false
      }),
      Unocss()
    ],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      }
    }
  }
})
