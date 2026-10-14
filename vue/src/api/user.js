import request from '@/api/request'

const base = '/user'

// 登录
export function login(data) {
  return request({
    url: base + '/login',
    method: 'post',
    data
  })
}

export function register(data) {
  return request({
    url: base + '/register',
    method: 'post',
    data
  })
}

export function idconfirm(data) {
  return request({
    url: base + '/idconfirm',
    method: 'post',
    data
  })
}

export function get(id) {
  return request({
    url: base + '/get/' + id,
    method: 'get',
  })
}

export function update(data) {
  return request({
    url: base + '/update',
    method: 'post',
    data
  })
}

export function modifypass(data) {
  return request({
    url: base + '/modifypass',
    method: 'post',
    data
  })
}
