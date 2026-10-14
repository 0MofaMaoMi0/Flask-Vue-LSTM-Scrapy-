<template>
  <v-card
    flat
    class="pa-3 mt-2"
  >
    <v-card-text class="d-flex">
      <v-avatar
        rounded
        size="120"
        class="me-6"
      >
        <v-img v-if="info.avatar==null" :src="avatar"></v-img>
        <v-img v-else :src="info.avatar"></v-img>
      </v-avatar>

      <!-- upload photo -->
      <div>
        <v-btn
          color="primary"
          class="me-3 mt-5"
          @click="$refs.refInputEl.click()"
        >
          <v-icon class="d-sm-none">
            {{ icons.mdiCloudUploadOutline }}
          </v-icon>
          <span class="d-none d-sm-block">上传新头像</span>
        </v-btn>

        <input
          ref="refInputEl"
          type="file"
          accept=".jpeg,.png,.jpg,GIF"
          :hidden="true"
          @change="getImages"
        />

        <v-btn
          color="error"
          outlined
          class="mt-5"
        >
          重置
        </v-btn>
        <p class="text-sm mt-5">
          允许 JPG, GIF 和 PNG. 不大于  800K
        </p>
      </div>
    </v-card-text>

    <v-card-text>
      <v-form class="multi-col-validation mt-6">
        <v-row>
          <v-col
              md="6"
              cols="12"
          >
            <v-text-field
                v-model="info.realname"
                label="姓名"
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
                v-model="info.idno"
                label="身份证号"
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
              v-model="info.username"
              label="昵称"
              dense
              outlined
            ></v-text-field>
          </v-col>

          <v-col
            md="6"
            cols="12"
          >
            <v-text-field
              v-model="info.phone"
              label="手机号"
              dense
              outlined
            ></v-text-field>
          </v-col>

          <v-col
            cols="12"
            md="6"
          >
            <v-text-field
              v-model="info.email"
              label="E-mail"
              dense
              outlined
            ></v-text-field>
          </v-col>

          <v-col
            cols="12"
            md="6"
          >
            <v-text-field
              v-model="info.age"
              dense
              label="年龄"
              outlined
            ></v-text-field>
          </v-col>

          <v-col
            cols="12"
            md="6"
          >
            <v-select
              v-model="info.addr"
              dense
              outlined
              label="住址"
              :items="status"
            ></v-select>
          </v-col>

          <v-col
            cols="12"
            md="6"
          >
            <v-text-field
              v-model="info.intro"
              dense
              outlined
              label="个人签名"
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
                    必须点击保存后，用户信息数据才会传到系统后台.
                  </p>
                  <a
                      href="javascript:void(0)"
                      class="text-decoration-none warning--text"
                  >
                    <span class="text-sm">特别注意</span>
                  </a>
                </div>
              </div>
            </v-alert>
          </v-col>

          <v-col cols="12">
            <v-btn
              color="primary"
              class="me-3 mt-4"
              @click="save"
            >
              保存修改
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
import {doupload} from "../../api/upload";
import {get, update} from "../../api/user";
import mixin from "../../mixins/mixins";

export default {
  components:{},
  mixins: [mixin],
  props: {
    accountData: {
      type: Object,
      default: () => {},
    },
  },
  data : () => ({
    status: ['江苏省', '北京市', '上海市', '深圳市'],
    accountDataLocale: [],
    icons: {
      mdiAlertOutline,
      mdiCloudUploadOutline,
    },
    avatar: require('@/assets/images/avatars/1.png'),
    info: {}
  }),
  async mounted() {
    this.accountDataLocale = JSON.parse(JSON.stringify(this.accountData))
    await get(this.uid).then(res => {
       this.info = res.data.data
      // console.log(this.info)
    })
  },
  methods:{
    resetForm(){
      this.accountDataLocale.value = JSON.parse(JSON.stringify(this.accountData))
    },
    getImages(el){
      const file = el.target.files[0]
      console.log(file)
      const type = file.type.split("/")[0]
      if (type === "image"){
        doupload(file).then(res=>{
          this.avatar = this.serverImg(res.data.data)
          this.info.avatar = this.serverImg(res.data.data)
        })
      }else{
        this.$snackbar({content: '只能上传图片格式', top:true, center:true, color:'red',multiLine: true})
      }
    },
    save(){
      const form = {"id" :this.uid, "phone": this.info.phone, "email": this.info.email, "addr": this.info.addr ,
      "avatar": this.info.avatar, "intro": this.info.intro , "age":this.info.age, "realname": ""}
      update(form).then(()=>{
        this.$store.dispatch('setAvatar', this.info.avatar)  //设置一下头像
        this.$snackbar({content: '更新成功', top:true, center:true, color:'green',multiLine: true})
      })
    }
  },
}
</script>
