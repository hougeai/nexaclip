// 全局配置：后端 REST 基址
// 生产填 Space 域名（含 /api/v1），开发留空走 vite proxy 的 /api/v1
export const API_BASE = import.meta.env.VITE_API_BASE || '/api/v1'
