# -*- coding: utf-8 -*-
"""Aliyun SMS integration configured from the local `.env` file."""

from aliyunsdkcore.client import AcsClient
from aliyunsdkcore.request import CommonRequest

from config import optional, required


class Sms:
    def __init__(self):
        self.client = AcsClient(
            required("ALIYUN_SMS_ACCESS_KEY_ID"),
            required("ALIYUN_SMS_ACCESS_KEY_SECRET"),
            optional("ALIYUN_SMS_REGION", "cn-hangzhou"),
        )
        self.common_request = CommonRequest()
        self.common_request.set_accept_format("json")
        self.common_request.set_domain("dysmsapi.aliyuncs.com")
        self.common_request.set_method("POST")
        self.common_request.set_protocol_type("https")
        self.common_request.set_version("2017-05-25")

    def sendCode(self, phone, code):
        self.common_request.set_action_name("SendSms")
        self.common_request.add_query_param("PhoneNumbers", phone)
        self.common_request.add_query_param("SignName", "大数据旅游网")
        self.common_request.add_query_param("TemplateCode", "SMS_238136898")
        self.common_request.add_query_param("TemplateParam", "{\"code\":\"%s\"}" % code)
        response = self.client.do_action(self.common_request)
        print(str(response, encoding="utf-8"))
        return response
