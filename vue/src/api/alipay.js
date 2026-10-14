import request from '@/api/request'

const base = '/alipay'

// 阿里支付
export function pay(data) {
  return request({
    url: base + '/pay',
    method: 'post',
    data
  })
}
