<template>
  <v-card
      flat
      class="pa-3 mt-2"
  >
    <v-card-text>
      <v-form class="multi-col-validation mt-6">
        <v-row>
          <v-col
              md="4"
              cols="8"
          >
            <v-text-field
                v-model="phone"
                label="手机号"
                dense
                disabled
                outlined
            ></v-text-field>

          </v-col>
          <v-col
              md="1"
              cols="4"
          >
            <v-btn @click="sendSms"
                   :loading="loading"
                   :disabled="isDisabled"
                   color="secondary">{{buttonName}}</v-btn>
          </v-col>
        </v-row>
        <v-row>
          <v-col
              md="6"
              cols="12"
          >
            <v-text-field
                v-model="code2"
                label="验证码"
                dense
                outlined
            ></v-text-field>
          </v-col>
        </v-row>
        <v-row>
          <v-col
              md="6"
              cols="12"
          >
            <v-text-field
                v-model="pass"
                label="输入新密码"
                dense
                outlined
            ></v-text-field>
          </v-col>
        </v-row>
        <v-row>
          <v-col
              md="6"
              cols="12"
          >
            <v-text-field
                v-model="pass2"
                label="再次输入新密码"
                dense
                outlined
            ></v-text-field>
          </v-col>
        </v-row>
        <v-row>
          <!-- alert -->
          <v-col cols="12">
            <v-alert
                color="warning"
                text
                class="mb-0"
            >
              <div class="d-flex align-start">
                <v-icon color="warning">
                  {{ icons.mdiAlertOutline }}
                </v-icon>

                <div class="ms-3">
                  <p class="text-base font-weight-medium mb-1">
                    请输入真实手机号，否则无法收到短信验证码.
                  </p>
                </div>
              </div>
            </v-alert>
          </v-col>
          <v-col cols="12">
            <v-btn
                color="blue"
                class="me-3 mt-4"
                @click.prevent="save"
            >
              提交
            </v-btn>
            <v-btn
                color="secondary"
                outlined
                class="mt-4"
                type="reset"
                @click.prevent="resetForm"
            >
              取消
            </v-btn>
          </v-col>
        </v-row>
      </v-form>
    </v-card-text>
  </v-card>
</template>

<script>
import { mdiAlertOutline, mdiCloudUploadOutline } from '@mdi/js'
import mixin from "../../mixins/mixins";
import {get, modifypass} from "../../api/user";
import {pay} from "../../api/alipay";
import {addOrder} from "../../api/order";
import {sendSms} from "../../api/sms";

export default {
  components:{ },
  mixins: [mixin],
  props: {
    accountData: {
      type: Object,
      default: () => {},
    },
  },
  data : () => ({
    files: [],
    idnum : '',
    name :'',
    icons: {
      mdiAlertOutline,
      mdiCloudUploadOutline,
    },
    info: {},
    addBal: 0,
    buttonName: "获取验证码",
    loading: false,
    isDisabled: false,
    time: 60,
    code: '',
    pass: '',
    pass2: '',
    code2: ''
  }),
  computed: {
  },
  async mounted() {
    console.log('phone', this.phone)
    await get(this.uid).then(res => {
      this.info = res.data.data
      // console.log(this.info)
    })
  },
  methods:{
    resetForm(){
      this.addBal = 0
    },
    save(){
      if(this.pass=='' || this.pass2=='' || this.code2==''){
        this.$snackbar({content: '表单必须输入完整', top:true, center:true, color:'red',multiLine: true})
        return
      }
      if(this.code!=this.code2){
        this.$snackbar({content: '验证码不正确！', top:true, center:true, color:'red',multiLine: true})
        return
      }
      if(this.pass!=this.pass2){
        this.$snackbar({content: '量词密码输入不一致！', top:true, center:true, color:'red',multiLine: true})
        return
      }
      modifypass({'id': this.uid, 'password': this.pass}).then(()=>{
        this.$snackbar({content: '修改成功', top:true, center:true, color:'green',multiLine: true})
      })
    },
    sendSms(){
      this.loading = true
      const formData = {'phone': this.phone}
      sendSms(formData).then(res=>{
        // console.log(res)
        this.loading = false
        if(res.data.code==0){
          this.code = res.data.data
          this.$snackbar({content: res.data.msg, top:true, center:true, color:'green',multiLine: true})
        }
        else{
          this.$snackbar({content: res.data.msg, top:true, center:true, color:'red',multiLine: true})
          return
        }
        let me = this;
        me.isDisabled = true;
        let interval = window.setInterval(function () {
          me.buttonName = me.time + '后重新发送';
          --me.time;
          if (me.time < 0) {
            me.buttonName = "重新发送";
            me.time = 60;
            me.isDisabled = false;
            window.clearInterval(interval);
          }
        }, 1000);
      })
    },
  },
}
</script>

<style>

</style>
