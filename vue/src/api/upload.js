import request from '@/api/request'

const base = '/file'

// 上传图片
export function upload(data) {
  return request({
    url: base + '/upload',
    method: 'post',
    data
  })
}

export function id_orc(data) {
  return request({
    url: base + '/idocr',
    method: 'post',
    data
  })
}

export function doupload(file) {
  var formdata = new FormData();
  formdata.append("myfile", file);
  return upload(formdata)
}

export function doidocr(file) {
  var formdata = new FormData();
  formdata.append("myfile", file);
  return id_orc(formdata)
}
