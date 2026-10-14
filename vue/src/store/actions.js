export default {
  setUid({commit}, params){
    localStorage.setItem('uid', params)
    commit('setUid', params)
  },
  setUserInfo({commit}, params){
    commit('setUserInfo', params)
  },
  setAvatar({commit}, params){
    commit('setAvatar', params)
  }
}
