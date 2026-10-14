import axios from 'axios'

const service =  axios.create({
    baseURL: '/api',
    timeout: 30000,
    // headers : {"Access-Control-Allow-Origin":"*"}
})

export default service
