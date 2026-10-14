import request from '@/api/request'

const base = '/deeplearning'

// 情感分析
export function senti_single(data) {
  return request({
    url: base + '/senti_single',
    method: 'post',
    data
  })
}

