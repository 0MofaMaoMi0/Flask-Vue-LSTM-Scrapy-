<template>
  <v-card id="account-setting-card">
    <!-- tabs -->
    <v-tabs
      v-model="tab"
      show-arrows
    >
      <v-tab
        v-for="tab in tabs"
        :key="tab.icon"
      >
        <v-icon
          size="20"
          class="me-3"
        >
          {{ tab.icon }}
        </v-icon>
        <span>{{ tab.title }}</span>
      </v-tab>
    </v-tabs>

    <!-- tabs item -->
    <v-tabs-items v-model="tab">
      <v-tab-item>
        <sentimental-analysis/>
      </v-tab-item>
    </v-tabs-items>
  </v-card>
</template>

<script>
import { mdiDatabaseSearchOutline  } from '@mdi/js'
// demos
import SentimentalAnalysis from "./SentimentalAnalysis";
// import AccountSettingsInfo from './AccountSettingsInfo.vue'

export default {
  components: {
    SentimentalAnalysis,
  },
  data: () => ({
    tab : '',
    tabs : [ { title: '评论实时情感分析', icon: mdiDatabaseSearchOutline },

    ],
    icons: {
      mdiDatabaseSearchOutline,
    },
  }),
}
</script>
