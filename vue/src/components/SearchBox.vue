<template>
  <v-autocomplete
    outlined
    rounded
    :loading="loading"
    dense
    clearable
    :menu-props="{ contentClass: 'transparent-scroll' }"
    :items="searchItems"
    item-text="title"
    color="#4edf93"
    :search-input.sync="searchTerm"
    no-data-text="尝试输入电影名..."
    placeholder="输入关键词"
  >
    <template #prepend-inner>
      <v-icon size="18" class="mt-1 mr-2">$search</v-icon>
    </template>
    <template #append>
      <v-icon @click="voiceSearch" size="21">$microphone</v-icon>
    </template>

    <template #item="{item}" class="mt-12">
      <v-list-item two-line :to="`/search?q=${item.title}`">
        <v-list-item-content class="text-left">
          <v-list-item-title>{{ item.title }}</v-list-item-title>
          <v-list-item-subtitle>{{ item.type }}</v-list-item-subtitle>
        </v-list-item-content>
      </v-list-item>
    </template>
  </v-autocomplete>
</template>

<script>
import {get} from "../api/movie";
import lodash from "lodash"

export default {
  name: "search-box",
  data: () => ({
    searchTerm: "",
    loading: false,
    searchItems: [],
  }),
  watch: {
    searchTerm() {
      this.doSearch()
    },
  },
  methods: {
    voiceSearch() {},
    doSearch: _.debounce(function(){
      console.log("searchTerm",this.searchTerm)

      if (!this.searchTerm)
        this.searchItems = [];
      this.loading = true;
      if(this.searchTerm == null)
        return

      get({"keyword": this.searchTerm}).then(res=>{
        this.$emit('search-movie', res.data.data)
        // console.log(res.data.data)
        this.loading = false
      })

      this.loading = false;
      // console.log(this.searchTerm)
      //在下面进行搜索
    },2000)
  },
};
</script>

<style>
.transparent-scroll {
  scrollbar-width: thin;
  scrollbar-color: transparent;
}
.transparent-scroll::-webkit-scrollbar {
  width: 6px;
  background-color: transparent;
}
.transparent-scroll::-webkit-scrollbar-thumb {
  background: #d8dcde;
  border-radius: 4.5px;
}
.transparent-scroll::-webkit-scrollbar-track {
  background-color: transparent;
}
</style>
