import request from '@/api/request'

// legacy-api: ignore-contract -- 旧旅游项目遗留模块，当前没有页面导入。

const base = '/'

/* 统计 */
export function hot_tour() {
  return request({
    url: base + 'hot_tour',
    method: 'get'
  })
}

export function city_rank() {
  return request({
    url: base + 'city_rank',
    method: 'get'
  })
}

export function tour_rank() {
  return request({
    url: base + 'tour_rank',
    method: 'get'
  })
}

export function province_rank(data) {
  return request({
    url: base + 'province_rank',
    method: 'post',
    data
  })
}

export function district_rank() {
  return request({
    url: base + 'district_rank',
    method: 'get'
  })
}

export function dash4() {
  return request({
    url: base + '/dash4',
    method: 'get'
  })
}

export function hotPlaceData() {
  return request({
    url: base + 'hotPlaceData',
    method: 'get'
  })
}
