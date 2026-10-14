import request from '@/api/request'

const base = '/movie'

export function getWordCut() {
  return request({
    url: base + '/getWordCut',
    method: 'get',
  })
}

export function get(data){
  return request({
    url: base + '/get',
    method: 'get',
    params: data
  })
}

export function getHot() {
  return request({
    url: base + '/getHot',
    method: 'get',
  })
}

export function getUserCF(userId) {
  return request({
    url: base + '/getRecomendation2',
    method: 'get',
    params: {'userId': userId}
  })
}

export function getItemCF(userId) {
  return request({
    url: base + '/getRecomendation',
    method: 'get',
    params: {'userId': userId}
  })
}

export function getChart1() {
  return request({
    url: base + '/getChart1',
    method: 'get',
  })
}


export function getAreaChart() {
  return request({
    url: base + '/getAreaChart',
    method: 'get',
  })
}

export function getChart2() {
  return request({
    url: base + '/getChart2',
    method: 'get',
  })
}


export function getChart3() {
  return request({
    url: base + '/getChart3',
    method: 'get',
  })
}

export function getTypeRank() {
  return request({
    url: base + '/getTypeRank',
    method: 'get',
  })
}

export function getNationRank() {
  return request({
    url: base + '/getNationRank',
    method: 'get',
  })
}

export function getTypeRate() {
  return request({
    url: base + '/getTypeRate',
    method: 'get',
  })
}


export function getTimeLine() {
  return request({
    url: base + '/getTimeLine',
    method: 'get',
  })
}

export function getComments(data) {
  return request({
    url: base + '/getComments',
    method: 'post',
    data
  })
}
