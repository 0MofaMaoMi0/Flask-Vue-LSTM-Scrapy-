import request from '@/api/request'

const base = '/order'

// 订单添加
export function addOrder(data) {
  return request({
    url: base + '/add',
    method: 'post',
    data
  })
}
