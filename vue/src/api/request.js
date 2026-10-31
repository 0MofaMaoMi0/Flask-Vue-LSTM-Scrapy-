import axios from 'axios'

const service = axios.create({
  baseURL: '/api',
  timeout: 30000
})

// 统一错误出口：
// - 业务失败（code !== 0）→ reject 一个带 code 的 Error，调用方只管 catch
// - 网络/超时 → reject 一句中文提示，避免每个组件各写一遍
service.interceptors.response.use(
  response => {
    const body = response.data
    if (body && typeof body === 'object' && 'code' in body && body.code !== 0) {
      const error = new Error(body.msg || '请求失败')
      error.code = body.code
      error.payload = body
      return Promise.reject(error)
    }
    return response
  },
  error => {
    let message = '网络异常，请稍后重试'
    if (error.response) {
      message = '服务异常（HTTP ' + error.response.status + '）'
    } else if (error.code === 'ECONNABORTED') {
      message = '请求超时，请稍后重试'
    }
    const wrapped = new Error(message)
    wrapped.cause = error
    return Promise.reject(wrapped)
  }
)

export default service