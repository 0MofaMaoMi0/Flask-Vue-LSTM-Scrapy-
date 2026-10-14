<template>
  <v-card
      flat
      class="pa-3 mt-2"
  >
    <v-card-text>
      <v-form class="multi-col-validation mt-6">
        <v-row>

          <v-col
              md="6"
              cols="12"
          >
            <v-text-field
                v-model="info.bal"
                label="余额"
                dense
                outlined
                disabled
            ></v-text-field>
          </v-col>

          <v-col
              md="6"
              cols="12"
          >
            <v-text-field
                v-model="addBal"
                label="充值金额"
                dense
                outlined
            ></v-text-field>
          </v-col>

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
                    充值功能使用支付宝沙箱进行模拟.
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
              支付
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
import {get} from "../../api/user";
import {pay} from "../../api/alipay";
import {addOrder} from "../../api/order";

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
    addBal: 0
  }),
  computed: {
  },
  async mounted() {
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
      if(this.addBal==0){
        this.$snackbar({content: '必须输入充值金额', top:true, center:true, color:'red',multiLine: true})
        return
      }
      const subject = "充值" //订单主题
      addOrder({'uid':this.uid, 'amount':this.addBal, 'type':subject}).then(res=>{
        const orderId = res.data.data

        pay({"orderId":orderId, "addBal":this.addBal, "subject": subject}).then(res=>{
          const url = res.data.data
          window.open(url)
          // win.document.open(url)
          // win.document.write(url)
          // win.document.close()
          this.$snackbar({content: '跳转到支付页面', top:true, center:true, color:'blue',multiLine: true})
        })
      })

    }
  },
}
</script>

<style>

</style>
