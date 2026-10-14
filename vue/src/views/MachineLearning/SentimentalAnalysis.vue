<template>
  <v-card
    flat
    class="pa-3 mt-2"
  >
    <v-card-text>
      <v-form class="multi-col-validation mt-6">
        <v-row>
          <v-col
              md="12"
              cols="12"
          >
            <v-textarea
                v-model="info"
                color="teal"
                outlined
            >
            </v-textarea>
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
                    算法说明：该模块评论情感分析使用深度学习LSTM算法模型实现.
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
              @click="predict"
              :disabled="progress"
              :loading="progress"
            >
              <v-icon left>
                mdi-gender-male
              </v-icon>
              分析
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

    <v-dialog
        v-model="progress"
        hide-overlay
        persistent
        width="300"
    >
      <v-card
          color="teal"
          dark
      >
        <v-card-text>
          深度学习情感分析中，请稍等..
          <v-progress-linear
              indeterminate
              color="purple"
              class="mb-0"
          ></v-progress-linear>
        </v-card-text>
      </v-card>
    </v-dialog>

    <v-alert
        :value="alert"
        color="purple lighten-2"
        dark
        border="top"
        icon="mdi-home"
        transition="scale-transition"
        class="white--text text-h4 mb-2"
    >
        根据算法分析,该评论属于:
      <div v-if="label=='positive'">
        好评
        <v-icon
          large
          color="green"
      >
        mdi-thumb-up
      </v-icon>
      </div>
      <div v-if="label=='negative'">
        差评
        <v-icon
            large
            color="red"
        >
          mdi-thumb-down
        </v-icon>
      </div>
      概率{{score}}
      <v-spacer></v-spacer>
      <v-col class="shrink">
        <v-btn
            color="info"
            outlined
            @click="alert=!alert"
        >
          好的
        </v-btn>
      </v-col>
    </v-alert>
  </v-card>
</template>

<script>
import { mdiAlertOutline, mdiCloudUploadOutline } from '@mdi/js'
import mixin from "../../mixins/mixins";
import {senti_single} from "../../api/machinelearning";

export default {
  components:{},
  mixins: [mixin],
  props: {
  },
  data : () => ({
    info: "",
    progress: false,
    accountDataLocale: [],
    icons: {
      mdiAlertOutline,
      mdiCloudUploadOutline,
    },
    loading: false,
    alert: false,
    label: '',
    score: 0,
  }),
  async mounted() {
  },
  methods:{
    predict(){
      if(this.info==""){
        this.$snackbar({content: '请先输入评论内容', top:true, center:true, color:'red',multiLine: true})
        return
      }
      this.progress = true
      const form = {"data" :this.info}
      senti_single(form).then((res)=>{
        this.progress = false
        console.log(res.data.data)
        this.label = res.data.data[0].label
        this.score = res.data.data[0].prob.toFixed(4) * 100 + '%'
        this.alert = true
      })
    }
  },
}
</script>
