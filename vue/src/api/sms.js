import request from '@/api/request'

const base = '/sms'

// 阿里支付
export function sendSms(data) {
  return request({
    url: base + '/sendSms',
    method: 'post',
    data
  })
}
