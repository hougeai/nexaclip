import request from './http'

// 所有 HTTP 请求统一走 request 实例（路径对齐 backend /api/v1/*）
// refresh_token 刷新由 http.js 拦截器内部处理，不在此暴露
export const api = {
  // ---- 系统 ----
  health: () => request.get('/system/health'),

  // ---- 认证 ----
  sendVerifyCode: data => request.post('/base/verifycode', data),
  register: data => request.post('/base/register', data),
  login: data => request.post('/base/access_token', data),
  userinfo: () => request.get('/base/userinfo'),
  forgotPassword: data => request.post('/base/forgot_password', data),
  resetPassword: data => request.post('/base/reset_password', data),

  // ---- 采集下载（需登录）：提交链接即探测并自动下载，按 vid 轮询状态 ----
  probe: data => request.post('/media/probe', data),
  // form: FormData（字段 file）；onPct(percent) 上传进度回调
  upload: (form, onPct) =>
    request.post('/media/upload', form, {
      onUploadProgress: e => e.total && onPct?.(Math.round((e.loaded / e.total) * 100))
    }),
  pollVideos: ids => request.get('/media/videos', { params: { ids: ids.join(',') } }),
  removeVideo: vid => request.delete(`/media/${vid}`),
  retranscribe: vid => request.post(`/media/${vid}/retranscribe`),
  reanalyze: vid => request.post(`/media/${vid}/reanalyze`),
  reembed: vid => request.post(`/media/${vid}/reembed`),
  updateMeta: (vid, data) => request.post(`/media/${vid}/meta`, data), // {title?, uploader?}
  // 时间轴高光：生成标注（异步，轮询 highlight_status）/ 导出选中片段（merge=拼一个，否则打包 zip）
  genHighlights: vid => request.post(`/media/${vid}/highlights`),
  exportClips: (vid, data) => request.post(`/media/${vid}/highlights/clip`, data), // {points:[{start,end}], merge}
  // 翻译：语种/音色表下发 / srt 同步生成（文本供前端预览）/ 配音异步（轮询 dub_status）
  translateLangs: () => request.get('/media/translate/langs'),
  translate: (vid, data) => request.post(`/media/${vid}/translate`, data), // {lang_target}
  getSub: vid =>
    request.get(`/media/${vid}/file`, { params: { kind: 'sub' }, responseType: 'text' }),
  dub: (vid, data) => request.post(`/media/${vid}/dub`, data), // {lang_target, voice}
  historyGroups: () => request.get('/media/history/groups'),
  getVideo: vid => request.get(`/media/${vid}`),
  history: params => request.get('/media/history', { params }),
  stats: () => request.get('/media/stats'),

  // ---- 知识库（RAG）：全库检索 / 跨视频问答 / 回填索引（挂在 media 域下） ----
  kbSearch: data => request.post('/media/kb/search', data),
  kbAsk: data => request.post('/media/kb/ask', data),
  kbReindex: () => request.post('/media/kb/reindex'),
  kbStatus: () => request.get('/media/kb/status'),

  // ---- 订阅：频道/作者主页定时枚举自动采集（media 域下 /subs） ----
  subList: () => request.get('/media/subs'), // 返回 {subs, avatars}
  subCreate: data => request.post('/media/subs', data),
  subSettings: (sid, data) => request.post(`/media/subs/${sid}/settings`, data),
  subCheck: sid => request.post(`/media/subs/${sid}/check`),
  subReprobe: sid => request.post(`/media/subs/${sid}/reprobe`),
  subRemove: sid => request.delete(`/media/subs/${sid}`)
}

export default api
