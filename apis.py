#!/usr/bin/env python
# -*- coding: utf-8 -*-

import re
import web
import json

from web import ctx
from web.contrib.template import render_jinja

import requests

from addons.get_CET import CET
from addons.calc_GPA import GPA
from addons.zfr import ZF, Login
from addons.redis2s import rds
from addons.data_factory import KbJson, get_score_dict
from addons import errors

render = render_jinja('templates', encoding='utf-8')

urls = (
    '/', 'APIDoc',

    '/checkcode.gif', 'CheckCode',

    # user
    '/user/login.json', 'UserLogin',
    '/user/(score|timetable)/(\w+).json', 'UserRequest',
    '/user/(score|timetable)/(\w+)/(raw?).json', 'UserRequest',

    '/(score|timetable)/current_semester.json', "CurrentSemester",
    '/(score|timetable)/(current_semester?)/(raw?).json', "CurrentSemester",

    '/score/(gpa|all|former_cet).json', 'GPAHandler',
)


class APIDoc:
    """ API 文档 """
    def GET(self):
        return render.api_doc()


class BaseApi(object):
    """ Base Class of API
    """

    def json_dumps(self, json_source):

        return json.dumps(json_source, ensure_ascii=False)

    def json_response(self, data=None, message="Success"):
        """ 填入 status 信息 """
        json_source = {
            "status" : {
                "code" : web.ctx.status[:3],
                "message" : message
            },
        }
        if data: json_source.update({'data': data})
        return self.json_dumps(json_source)

    def json_request(self):

        json_content = web.input()['data']
        return json.loads(json_content)

class BaseGet(object):
    """ 基类
    默认GET返回UID
    """
    def GET(self, category=None, item=None, raw=False):

        try:
            zf = ZF()
            uid = zf.pre_login()
        except errors.ZfError, e:
            return self.json_response({}, message=e.value)

        return self.json_response(data={"uid":uid})

class UserLogin(BaseApi, BaseGet):
    """ 验证类 登录请求 """

    def POST(self):

        try:
            content = self.json_request()
        except:
            web.ctx.status = "400"
            return self.json_response(message="data format wrong")

        try:
            zf = Login()
            zf.login(content["uid"], content)
            return self.json_response(data={"uid":content["uid"]})
        except errors.PageError, e:
            return self.json_response({}, message=e.value)


class CheckCode(BaseApi):

    """ 验证码链接 """

    def GET(self):
        try:
            uid = web.input(_method='get').uid
        except AttributeError:
            return self.json_response({}, message='no UID')
        web.header('Content-Type','image/gif')
        zf = ZF()
        return zf.get_checkcode(uid)


class UserRequest(BaseApi):
    """ 验证类: 处理全部请求 """
    category_list = ('score', 'timetable')
    item_list = ('current_semester', 'last_semester','all', 'gpa', 'former_cet')

    def POST(self, category=None, item=None, raw=False):

        if category not in self.category_list or item not in self.item_list:
            raise web.notfound()
        try:
            content = self.json_request()
            self.uid = content['uid']
        except:
            web.ctx.status = "400"
            return self.json_response(message="data format wrong")

        try:
            self.xh = rds.hget(self.uid, "xh")

            if category == "score":
                if item == "former_cet":
                    cet = CET()
                    data = cet.get_cet_json(self.xh)
                elif item == "gpa":
                    gpa = GPA(self.xh)
                    data = {"gpa": gpa.get_gpa()}
                elif item == "all":
                    gpa = GPA(self.xh)
                    data = gpa.get_allscore_dict()
                elif item == "current_semester":
                    zf = Login()
                    zf.init_after_login(self.uid, self.xh)
                    score = zf.get_score()
                    data = get_score_dict(score)
                elif item == "last_semester":
                    zf = Login()
                    zf.init_after_login(self.uid, self.xh)
                    score = zf.get_last_score()
                    data = get_score_dict(score)
            elif category == "timetable":
                zf = Login()
                zf.init_after_login(self.uid, self.xh)

                if item == "current_semester":
                    timetable = zf.get_timetable()
                elif item == "last_semester":
                    timetable = zf.get_last_timetable()
                if raw:
                    data = {"raw": timetable}
                else:
                    k = KbJson(timetable)
                    data_list = k.get_list()
                    data = data_list
            return self.json_response(data)
        except (AttributeError, TypeError, KeyError, requests.TooManyRedirects), e:
            web.ctx.status = "401"
            return self.json_response({}, message="Need Login")
        except (errors.RequestError, errors.PageError), e:
            return self.json_response({}, message=e.value)


class CurrentSemester(BaseApi, BaseGet):
    """ 处理对当前学期的查询 """

    def POST(self, category=None, item=None, raw=False):

        try:
            content = self.json_request()
        except:
            web.ctx.status = "400"
            return self.json_response(message="data format wrong")
        try:
            zf = Login()
            zf.login(content["uid"], content)
            if category == "score":
                score = zf.get_score()
                data = get_score_dict(score)
            elif category == "timetable":
                timetable = zf.get_timetable()
                if raw:
                    data = {"raw": timetable}
                else:
                    k = KbJson(timetable)
                    data_list = k.get_list()
                    data = data_list
            return self.json_response(data)
        except errors.PageError, e:
            return self.json_response({}, message=e.value)

class GPAHandler(BaseApi):
    """ 获取全部成绩或者学分绩点 """

    def POST(self, category):
        try:
            content = self.json_request()
            self.xh = content['xh']
        except:
            web.ctx.status = "400"
            return self.json_response(message="data format wrong")
        if category == "former_cet":
            cet = CET()
            data = cet.get_cet_json(self.xh)
        else:
            gpa = GPA(self.xh)
            if category == "gpa":
                data = {"gpa": gpa.get_gpa()}
            elif category == "all":
                data = gpa.get_allscore_dict()
        return self.json_response(data)

def notfound():
    """404
    """
    base = BaseApi()
    web.ctx.status = "404"
    return web.notfound(base.json_response(message="Not Found"))

def internalerror():
    """500
    """
    base = BaseApi()
    web.ctx.status = "500"
    return web.internalerror(base.json_response(message="Internal Error"))


app = web.application(urls, locals())
app.notfound = notfound
app.internalerror = internalerror

