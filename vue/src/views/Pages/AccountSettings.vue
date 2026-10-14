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
        <account-settings-account :account-data="accountSettingData.account"></account-settings-account>
      </v-tab-item>

      <v-tab-item>
        <account-setting-i-d-confirm></account-setting-i-d-confirm>
      </v-tab-item>

      <v-tab-item>
          <account-settings-money/>
      </v-tab-item>

      <v-tab-item>
        <account-settings-password/>
      </v-tab-item>
    </v-tabs-items>
  </v-card>
</template>

<script>
import { mdiAccountOutline, mdiLockOpenOutline, mdiInformationOutline, mdiCashMultiple, mdiLockAlert   } from '@mdi/js'
// demos
import AccountSettingsAccount from './AccountSettingsAccount.vue'
import AccountSettingIDConfirm from './AccountSettingsIDConfirm.vue'
import AccountSettingsMoney from "./AccountSettingsMoney";
import AccountSettingsPassword from "./AccountSettingsPassword";

// import AccountSettingsInfo from './AccountSettingsInfo.vue'

export default {
  components: {
    AccountSettingsMoney,
    AccountSettingsAccount,
    AccountSettingIDConfirm,
    AccountSettingsPassword,
  },
  data: () => ({
    tab : '',
    tabs : [ { title: '账户信息', icon: mdiAccountOutline }
      
 
    ],
    accountSettingData : {
      account: {
        avatarImg: require('@/assets/images/avatars/1.png'),
        username: 'johnDoe',
        name: 'john Doe',
        email: 'johnDoe@example.com',
        role: 'Admin',
        status: 'Active',
        company: 'Google.inc',
      },
      information: {
        bio: 'The name’s John Deo. I am a tireless seeker of knowledge, occasional purveyor of wisdom and also, coincidentally, a graphic designer. Algolia helps businesses across industries quickly create relevant 😎, scaLabel 😀, and lightning 😍 fast search and discovery experiences.',
        birthday: 'February 22, 1995',
        phone: '954-006-0844',
        website: 'https://themeselection.com/',
        country: 'USA',
        languages: ['English', 'Spanish'],
        gender: 'male',
      },
    },
    icons: {
      mdiAccountOutline,
      mdiLockOpenOutline,
      mdiInformationOutline,
    },
  }),
}
</script>
