const path = require("path");

function resolve(dir){
  return path.join(__dirname, dir)
}

module.exports = {
  devServer: {
    port: 8999, // 端口号配置
    // 路由使用 history 模式；直接打开 /home 等地址时也应返回 Vue 入口页。
    historyApiFallback: true,
    // open: true // 自动打开浏览器
    proxy: {
      "/api": {
        target: "http://localhost:8080",
        changeOrigin: true,
        ws: false,
        pathRewrite: {
          "^/api": ""
        }
      }
    }
  },
  transpileDependencies: [
    'vuetify'
  ],
  chainWebpack(config){
    // set svg-sprite-loader
    config.module
      .rule('svg')
      .exclude.add(resolve('src/icons'))
      .end()
    config.module
      .rule('icons')
      .test(/\.svg$/)
      .include.add(resolve('src/icons'))
      .end()
      .use('svg-sprite-loader')
      .loader('svg-sprite-loader')
      .options({
        symbolId: 'icon-[name]'
      })
      .end()
  },
  configureWebpack: {
    resolve:{
      alias:{
        '@': resolve('src')
      }
    }
  },
}
