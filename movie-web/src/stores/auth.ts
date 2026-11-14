import {defineStore} from 'pinia'
import {loadPersonalProfile} from '../services/personalFeatures'
import {call,refreshCsrf,clearCsrf,AuthError,onUnauthorized,type User} from '../api/auth'
export const useAuthStore=defineStore('auth',{
 state:()=>({user:null as User|null,ready:false}),
 actions:{
  async initialize(){
   onUnauthorized(()=>{this.user=null})
   await refreshCsrf()
   try{this.user=await call<User>('me')}catch(e){this.user=null;if(!(e instanceof AuthError&&e.status===401))throw e}
   if(this.user)await loadPersonalProfile(this.user.id).catch(()=>{})
   this.ready=true
  },
  async signedIn(){await refreshCsrf();this.user=await call<User>('me');await loadPersonalProfile(this.user.id).catch(()=>{});this.ready=true},
  async logout(){
   try{await refreshCsrf();await call('logout','POST',{})}
   catch(e){if(!(e instanceof AuthError&&e.status===401))throw e}
   this.user=null;this.ready=false;clearCsrf()
  }
 }
})
