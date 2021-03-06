#!/usr/bin/python2.7
# -*- coding: utf-8 -*-

import sys
reload(sys)
sys.setdefaultencoding('utf-8')
import logging
import datetime

import web
from web.contrib.template import render_jinja
from jinja2.exceptions import UndefinedError
import requests

from addons import config
from addons import errors
from addons.redis2s import rds
from addons import mongo2s
from addons.get_CET import CET
from addons.zfr import ZF, Login
from addons.autocache import redis_memoize
from addons import get_former_cet, get_book
from addons.RedisStore import RedisStore
from addons.utils import get_score_gpa, PageAlert
from addons.utils import incr_key
from forms import cet_form, xh_form, login_form, cron_form


urls = (
    '/', 'Index',

    '/zheng', 'ZhengFang',
    '/zheng/nocode', 'ZhengFangNoCode',
    '/zheng/checkcode', 'CheckCode',
    '/more/(.+)', 'More',
    '/score', 'Score',

    '/cet', 'Cet',
    '/cet/former', 'FormerCET',
    '/libr', 'Libr',

    '/cron.html', 'Cronwork',
    '/ad/(.+)', 'Advertise',

    '/(\w+).html', 'TemplatePage',
)

# main app
app = web.application(urls, globals(),autoreload=False)

# session
session = web.session.Session(app, RedisStore(), {'count': 0, 'xh':False})

# render templates
from addons.config import domains
render = render_jinja('templates', encoding='utf-8',
                      globals=dict(context=session,domains=domains,
                            alert=PageAlert(),))

# init mongoDB
mongo = mongo2s.init_mongo()



class Index:

    @redis_memoize(100, "Index")
    def GET(self):
        return render.index()

class BaseSearch(object):
    """ 各个查询功能的基类
    """

# 成绩查询
class ZhengFang:

    def GET(self):
        try:
            zf = ZF()
            uid = zf.pre_login()
        except errors.RequestError, e:
            return render.serv_err(err=e.value)
        session['uid'] = uid
        import time
        return render.zheng(ctime=str(time.time()))

    def POST(self):
        content = web.input()
        try:
            session['xh'] = content['xh']
            t = content['type']
            uid = session['uid']
        except (AttributeError, KeyError), e:
            return render.alert_err(error='请检查您是否禁用cookie', url='/zheng')
        try:
            zf = Login()
            zf.login(uid, content)
            __dic = {
                    '1': zf.get_score,
                    '2': zf.get_kaoshi,
                    '3': zf.get_timetable,
                    '4': zf.get_last_timetable,
                    '5': zf.get_last_score,
                    }
            if t not in __dic.keys():
                return render.alert_err(error='输入不合理', url='/zheng')
            return render.result(tables=__dic[t]())
        except errors.PageError, e:
            return render.alert_err(error=e.value, url='/zheng')
        except errors.RequestError, e:
            return render.serv_err(err=e)

class CheckCode:
    """验证码链接
    """
    def GET(self):
        try:
            uid = web.input(_method='get').uid
        except AttributeError:
            try:
                uid = session['uid']
                if not uid: raise KeyError
            except KeyError:
                return render.serv_err(err='该页面无法直接访问或者您的登录已超时，请重新登录')
        web.header('Content-Type','image/gif')
        zf = ZF()
        try:
            image_content = zf.get_checkcode(uid)
            return image_content
        except errors.PageError, e:
            pass

class More:
    """连续查询 二次查询
    """
    def GET(self, t):
        try:
            if session['xh'] is False:
                raise KeyError
        except KeyError:
            raise web.seeother('/zheng')
        try:
            __dic1 = { # need xh
                    'oldcet':get_former_cet,
                    }
            if t in __dic1.keys():
                return render.result(table=__dic1[t](session['xh']))

            elif t=='score':
                try:
                    score, jidi=get_score_gpa(session['xh'])
                except errors.PageError, e:
                    return render.alert_err(error=e.value, url='/score')
                return render.result(table=score, jidian=jidi)
            #elif t=='morekb':
            zf = Login()
            __dic = { # just call
                    'zheng': zf.get_score,
                    'kaoshi': zf.get_kaoshi,
                    'kebiao': zf.get_timetable,
                    'lastkebiao': zf.get_last_timetable,
                    'lastscore': zf.get_last_score,
                    }
            if t in __dic.keys():
                zf.init_after_login(session['uid'], session['xh'])
                return render.result(tables=__dic[t]())
            raise web.notfound()
        except (AttributeError, TypeError, KeyError):
            raise web.seeother('/zheng/nocode')
        except errors.RequestError, e:
            return render.serv_err(err=e)
        except errors.PageError, e:
            return render.alert_err(error=e.value, url='/score')

class ZhengFangNoCode:
    """
    无验证码正方登录
    """
    title='正方教务系统'

    @redis_memoize(100, "ZhengFangNoCode")
    def GET(self):
        form=login_form
        return render.normal_form(title=self.title, form=form)

    def POST(self):
        form=login_form()
        if not form.validates():
            return render.normal_form(title=self.title,form=form)
        else:
            try:
                zf = Login()
                session['uid'] = zf.no_code_login(form.d)
                session['xh'] = form.d.xh
                return render.result(tables=zf.get_timetable())
            except errors.PageError, e:
                return render.alert_err(error=e.value, url='/libr')

class Cet:
    """ 四六级成绩查询
    当前使用 JAE 接口进行查询
    """
    @redis_memoize(100, 'Cet')
    def GET(self):
        form = cet_form()
        return render.cet(form=form)

    def POST(self):
        form = cet_form()
        try:
            if not form.validates():
                return render.cet(form=form)
            else:
                zkzh = form.d.zkzh
                name = form.d.name
                name = name.encode('utf-8')
                from addons.get_CET import get_cet_fm_jae
                table = get_cet_fm_jae(zkzh, name)
                return render.result(single_table=table)
        except UnicodeDecodeError:
            rds.hset('Error:Hash:CET:UnicodeDecodeError', form.d.zkzh, form.d.name)
            return render.cet(form=form)


class FormerCET:
    """
    往年cet成绩查询
    """
    @redis_memoize(1000, 'FormerCET')
    def GET(self):
        form=xh_form
        title='往年四六级成绩'
        return render.normal_form(title=title, form=form)
    def POST(self):
        form = xh_form()
        title='往年四六级成绩'
        if not form.validates():
            return render.normal_form(title=title, form=form)
        else:
            xh = form.d.xh
            session['xh']=xh
        try:
            table=get_former_cet(xh)
            return render.result(table=table)
        except errors.PageError, e:
            return render.alert_err(error=e.value, url='/cet/former')
        except errors.RequestError, e:
            return render.serv_err(err=e)


class Libr:
    """
    图书馆相关
    """
    @redis_memoize(10000, 'Libr')
    def GET(self):
        form=login_form
        title='图书馆借书查询'
        return render.normal_form(title=title, form=form)

    def POST(self):
        form=login_form()
        title='图书馆借书查询'
        if not form.validates():
            return render.normal_form(title=title,form=form)
        else:
            xh, pw=form.d.xh, form.d.pw
            session['xh']=xh
        try:
            table=get_book(xh,pw)
        except errors.PageError, e:
            return render.alert_err(error=e.value, url='/libr')
        return render.result(table=table)


# 全部成绩
class Score:

    @redis_memoize(10000, "Score")
    def GET(self):
        form = xh_form()
        # alert=rds.get('SINGLE_score')
        return render.score(form=form)

    def POST(self):
        form = xh_form()
        if not form.validates():
            return render.score(form=form)
        else:
            xh = form.d.xh
            session['xh']=xh
            try:
                score, jidi=get_score_gpa(xh)
            except errors.PageError, e:
                return render.alert_err(error=e.value)
            except errors.RequestError, e:
                return render.serv_err(err=e)

            return render.result(table=score, jidian=jidi)

            # else:
            #    return "成绩查询源出错,请稍后再试!"

# 平均学分绩点计算说明页面


# class HelpGpa:
#
#     @redis_memoize('help_gpa')
#     def GET(self):
#         return render.help_gpa()

# 评论页面, 使用多说评论
#
# class Comment:
#
#     @redis_memoize('comment')
#     def GET(self):
#         return render.comment()
#
#
# class Contact:
#
#     """contact us page"""
#     @redis_memoize('contanct')
#     def GET(self):
#         return render.contact()

# notice

# class Notice:
#     @redis_memoize('notice')
#     def GET(self):
#         news = mongo.notice.find().sort("datetime",-1)
#         return render.notice(news=news)

class Advertise:
    """广告处理
    - 递增PV
    - 渲染模板
    """
    def GET(self, ad_name):
        ads = ('',)
        if ad_name in ads:
            incr_key('AD:Count:%s' % ad_name)
            return getattr(render, ad_name)()
        else:
            raise web.notfound()

class TemplatePage:
    """Simple Template File"""

    def GET(self, templ):
        templates = ('comment', 'contact', 'donate',
                     'HelpGPA', 'notice')
        if templ not in templates:
            raise web.notfound()
        @redis_memoize(1000, templ)
        def dispose(templ):
            kwargs = dict()
            if templ == "notice":
                kwargs['news'] = mongo.notice.find().sort("datetime",-1)
            elif templ == "donate":
                kwargs['sponsor'] = mongo.donate.find().sort("much",-1)
            return getattr(render, templ)(**kwargs)

        return dispose(templ)


class Donate:

    @redis_memoize('donate')
    def GET(self):
        sponsor = mongo.donate.find().sort("much",-1)
        return render.donate(sponsor=sponsor)

class Cronwork:

    def GET(self):
        action = web.input(_method='get').get("action", None)
        if action and action == "apply":
            title = "申请推送服务"
            form = cron_form()
            return render.normal_form(title=title, form=form)
        return render.cronpage()

    def POST(self):
        action = web.input(_method='get').get("action", None)
        if action and action == "apply":
            form = cron_form()
            if form.validates():
                mongo["users"].insert({
                    'name': form.d['name'],
                    'email' : form.d["email"],
                    'xh': form.d['xh'],
                    'pw': form.d['pw'],
                    # 'lib_pw': form.d['lib_pw'],
                    'alipay': form.d['alipay'],
                    'created_date': datetime.datetime.now(),
                    'updated_date': datetime.datetime.now(),
                    'active': False,
                })
                return render.alert_err(
                    error='申请完成，等待管理员检查，最长24小时内答复，谢谢支持！',
                    url='/')
            else:
                title = "申请推送服务"
                return render.normal_form(title=title, form=form)

# web server
def session_hook():
    """ share session with sub apps
    """
    web.ctx.session = session

def notfound():
    """404
    """
    return web.notfound(render.notfound())

def internalerror():
    """500
    """
    web.setcookie('webpy_session_id','',-1)
    incr_key('Error:InternalError')
    return web.internalerror(render.internalerror())

app.notfound = notfound
app.internalerror = internalerror
app.add_processor(web.loadhook(session_hook))

# for Gunicorn
application = app.wsgifunc()
