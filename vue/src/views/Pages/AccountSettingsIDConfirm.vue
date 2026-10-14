<template>
  <v-card
      flat
      class="pa-3 mt-2"
  >
    <v-card-text class="d-flex">
      <v-avatar
          rounded
          size="120"
          width="120"
          height="80"
          class="me-6">
        <v-img :src="idcardImage"></v-img>
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
          <span class="d-none d-sm-block">上传身份证</span>
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
                v-model="name"
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
                v-model="idnum"
                label="身份证号码"
                dense
                outlined
                disabled
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
                    必须点击保存后，身份认证数据才会传到系统后台.
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
                @click.prevent="save"
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
import {doidocr} from "../../api/upload";
import {idconfirm} from "../../api/user";
import mixin from "../../mixins/mixins";

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
    idcardImage: require('@/assets/images/id-card.png')
  }),
  computed: {
    // Determine the URL to show, typically from an object representing a user
    url () {
      return 'https://randomuser.me/api/portraits/men/1.jpg'
    }
  },
  mounted() {

  },
  methods:{
    resetForm(){
      this.idnum = ''
      this.name = ''
    },
    getImages(el){
      const file = el.target.files[0]
      // console.log(file)
      const type = file.type.split("/")[0]
      if (type === "image"){
        doidocr(file).then(res=>{
          // console.log(res.data)
          this.idnum = res.data.data.idno
          this.name = res.data.data.name
          this.idcardImage = this.serverImg(res.data.data.pic)
          console.log(this.idcardImage)
        })
      }else{
        this.$snackbar({content: '只能上传图片格式', top:true, center:true, color:'red',multiLine: true})
      }
    },
    save(){
      idconfirm({'id': this.uid, 'idno': this.idnum, 'realname': this.name}).then(()=>{
        this.$snackbar({content: '更新成功', top:true, center:true, color:'green',multiLine: true})
        //
        // console.log(res)
      })
    }
  },
}
</script>

<style>

</style>
