# -*- encoding: utf-8 -*-
"""重构自测：验证适配器注册表 / 配置加载 / URL 解析 / WebUI 应用。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []


def check(name, cond, detail=''):
    if cond:
        print(f'  ✓ {name}')
    else:
        failures.append(name)
        print(f'  ✗ {name} {detail}')


# ---------- 1. 适配器注册表 ----------
print('[1] 适配器系统')
from src.adapters import registry, ResolveContext, match

all_ads = registry.all()
check('注册平台数 >= 50', len(all_ads) >= 50, f'实际 {len(all_ads)}')
print(f'      注册平台: {len(all_ads)} 个, 示例: {[a.name for a in all_ads[:8]]}')

test_urls = {
    '抖音': ('https://live.douyin.com/745964462470', '抖音直播'),
    'TikTok': ('https://www.tiktok.com/@pearlgaga88/live', 'TikTok直播'),
    'B站': ('https://live.bilibili.com/21593109', 'B站直播'),
    '虎牙': ('https://www.huya.com/116', '虎牙直播'),
    '自定义m3u8': ('https://example.com/live/stream.m3u8', '自定义录制直播'),
    '自定义flv': ('http://example.com/live/stream.flv', '自定义录制直播'),
    '小红书短链': ('https://xhslink.com/a/abc123', '小红书直播'),
}
for name, (url, expect) in test_urls.items():
    ad = match(url)
    check(f'匹配[{name}] → {expect}', ad is not None and ad.name == expect,
          f'实际 {ad.name if ad else None}')

ad = match('https://unknown-platform.com/room/123')
check('未知平台返回 None', ad is None)

# 元数据
douyin = match('https://live.douyin.com/123')
check('抖音 flv_preferred', douyin.flv_preferred)
check('抖音 clean_url', douyin.clean_url)
shopee = match('https://live.shopee.sg/live/xxx')
check('shopee only_flv + http_force + overseas',
      shopee and shopee.only_flv and shopee.http_force and shopee.overseas)
tiktok = match('https://www.tiktok.com/@x/live')
check('tiktok force_proxy', tiktok and tiktok.force_proxy)
check('tiktok overseas', tiktok and tiktok.overseas)
maoer = match('https://fm.missevan.com/live/123')
check('猫耳FM only_audio', maoer and maoer.only_audio)
wink = match('https://www.winktv.co.kr/123')
check('WinkTV headers', wink and wink.get_headers('https://www.winktv.co.kr/123') == 'origin:https://www.winktv.co.kr')
shopee_headers = shopee.get_headers('https://shopee.sg/live/1')
check('shopee 动态 origin header', shopee_headers == 'origin:https://shopee.sg')

# 平台 ID → URL 构造
bili = match('https://live.bilibili.com/123')
check('B站 build_url', bili.build_url('21593109') == 'https://live.bilibili.com/21593109')
tt = match('https://www.tiktok.com/@x/live')
check('TikTok build_url 去@', tt.build_url('@pearlgaga88') == 'https://www.tiktok.com/@pearlgaga88/live')
check('TikTok build_url 无@', tt.build_url('pearlgaga88') == 'https://www.tiktok.com/@pearlgaga88/live')
dy = match('https://live.douyin.com/123')
check('抖音 build_url', dy.build_url('745964462470') == 'https://live.douyin.com/745964462470')
huya = match('https://www.huya.com/116')
check('虎牙 build_url', huya.build_url('116') == 'https://www.huya.com/116')
check('空ID返回空', huya.build_url('  ') == '')
wink = match('https://www.winktv.co.kr/123')
check('无模板平台 build_url 返回空', wink.build_url('123') == '')

# 适配器 resolve 的代理检查（不真正发请求）
from src.adapters import TwoStepAdapter


class _Fake(TwoStepAdapter):
    name = '测试代理平台'
    hosts = ('fake-proxy-test.com',)
    overseas = True
    force_proxy = True

    async def fetch(self, url, ctx):
        return {'ok': 1}

    async def build(self, data, ctx):
        return data


import asyncio
ctx = ResolveContext(proxy=None, global_proxy=False)
r = asyncio.run(_Fake().resolve('https://fake-proxy-test.com/1', ctx))
check('force_proxy 无代理返回 None', r is None)
ctx2 = ResolveContext(proxy='http://127.0.0.1:7890', global_proxy=True)
r2 = asyncio.run(_Fake().resolve('https://fake-proxy-test.com/1', ctx2))
check('有代理正常 resolve', r2 == {'ok': 1})

# ---------- 2. 配置加载 ----------
print('[2] 配置加载')
from src import config as app_config

tmpdir = tempfile.mkdtemp()
cfg_path = os.path.join(tmpdir, 'config.ini')
cfg = app_config.load_config(cfg_path)
check('默认值: 保存格式 TS', cfg.video_save_type == 'TS')
check('默认值: 画质 原画', cfg.video_record_quality == '原画')
check('默认值: 循环 120s', cfg.delay_default == 120)
check('默认值: 磁盘阈值 1.0', cfg.disk_space_limit == 1.0)
check('默认值: 代理平台列表', 'tiktok' in (cfg.enable_proxy_platform_list or []))
check('默认值: cookie dict 非空', len(cfg.cookies) >= 40, f'实际 {len(cfg.cookies)}')
check('默认值: partner_code', cfg.accounts.get('popkontv_partner_code') == 'P-00001')
check('默认值: 账号类型 normal', cfg.accounts.get('twitcasting_account_type') == 'normal')
check('config.ini 已生成', os.path.exists(cfg_path))

# 加载真实 config.ini
real_cfg = app_config.load_config('config/config.ini')
check('真实 config 加载', real_cfg.video_save_type in ('TS', 'FLV', 'MKV', 'MP4'))

# ---------- 3. URL 配置解析 ----------
print('[3] URL 配置解析')
from src.url_config import TaskStore, parse_entry

store_path = os.path.join(tmpdir, 'URL_config.ini')
with open(store_path, 'w', encoding='utf-8-sig') as f:
    f.write('# 注释示例\n')
    f.write('https://live.douyin.com/745964462470\n')
    f.write('超清,https://live.bilibili.com/21593109,测试主播\n')
    f.write('https://www.huya.com/116?foo=bar,主播: 虎牙一哥\n')
    f.write('https://unknown.xyz/room/1\n')
    f.write('https://www.tiktok.com/@test/live,主播: TT\n')
    f.write('https://example.com/live/a.m3u8\n')

store = TaskStore(store_path)
entries, unknown = store.load()
check('解析任务数 = 5', len(entries) == 5, f'实际 {len(entries)}')
check('未知链接 = 1', len(unknown) == 1, f'实际 {unknown}')
urls = {e.url for e in entries}
check('画质解析', next(e for e in entries if 'bilibili' in e.url).quality == '超清')
check('名称保留主播前缀', any('主播: 虎牙一哥' in e.name for e in entries))
check('默认画质', next(e for e in entries if 'douyin' in e.url).quality == '原画')
check('clean_url 去 query', all('foo=bar' not in e.url for e in entries))
check('m3u8 自定义任务', any('.m3u8' in e.url for e in entries))
# 注释行的处理：'# 注释示例' 行过短被跳过；tiktok 行正常
# 检查文件是否回写（未知链接被注释）
content = open(store_path, encoding='utf-8-sig').read()
check('未知链接已自动注释', '# https://unknown.xyz/room/1' in content)

# 增删改
ok = store.add('https://www.douyu.com/123', '高清', '测试')
check('add 合法平台', ok)
ok = store.add('https://bad.unknown/1')
check('add 非法平台拒绝', not ok)
check('remove', store.remove('https://www.douyu.com/123'))
check('set_commented', store.set_commented('https://www.tiktok.com/@test/live', True))
entries2, _ = store.load()
tt = next(e for e in entries2 if 'tiktok' in e.url)
check('暂停后 commented=True', tt.commented is True)

# ---------- 4. WebUI ----------
print('[4] WebUI')
from webui.app import create_app
from fastapi.testclient import TestClient

app = create_app(cfg_path, store_path, os.path.join(tmpdir, 'downloads'), tmpdir, 'v4.0.7-test')
client = TestClient(app)

r = client.get('/api/status')
check('GET /api/status', r.status_code == 200 and 'task_count' in r.json())
r = client.get('/api/tasks')
check('GET /api/tasks', r.status_code == 200 and 'tasks' in r.json())
r = client.get('/api/platforms')
check('GET /api/platforms', r.status_code == 200 and len(r.json()['platforms']) >= 50)
r = client.get('/api/logs')
check('GET /api/logs', r.status_code == 200)
r = client.get('/api/config')
check('GET /api/config 文本', r.status_code == 200)
r = client.get('/')
check('GET / 页面', r.status_code == 200 and 'DouyinLiveRecorder' in r.text)
r = client.post('/api/tasks', json={'url': 'https://live.douyin.com/999', 'quality': '超清', 'name': 'webui测试'})
check('POST /api/tasks', r.status_code == 200)
r = client.post('/api/tasks', json={'url': 'https://bad.unknown/1'})
check('POST 非法 URL 400', r.status_code == 400)
# 选平台 + 输 ID
r = client.post('/api/tasks/from-id', json={'platform': 'B站直播', 'id': '21593109', 'quality': '高清', 'name': 'ID快捷'})
check('POST from-id 成功', r.status_code == 200 and 'https://live.bilibili.com/21593109' in r.json().get('url', ''))
r = client.post('/api/tasks/from-id', json={'platform': 'TikTok直播', 'id': '@pearlgaga88'})
check('POST from-id TikTok 去@', r.status_code == 200)
r = client.post('/api/tasks/from-id', json={'platform': '不存在的平台', 'id': '1'})
check('POST from-id 未知平台 400', r.status_code == 400)
r = client.post('/api/tasks/from-id', json={'platform': 'WinkTV', 'id': '1'})
check('POST from-id 无模板平台 400', r.status_code == 400)
rp = client.get('/api/platforms')
check('platforms 含 url_template', rp.status_code == 200 and all('url_template' in p for p in rp.json()['platforms']))
check('platforms 含 id_placeholder', rp.status_code == 200 and any(p['id_placeholder'] for p in rp.json()['platforms']))
r = client.put('/api/config', content='[录制设置]\nlanguage(zh_cn/en) = zh_cn\n', headers={'content-type': 'text/plain'})
check('PUT /api/config', r.status_code == 200)
r = client.put('/api/config', content='这不是ini配置[[[', headers={'content-type': 'text/plain'})
check('PUT 非法配置 400', r.status_code == 400)
r = client.get('/api/videos')
check('GET /api/videos', r.status_code == 200)

# state 模块
from src import state
state.register_task('https://live.douyin.com/999', '超清', 'webui测试')
state.update_task('https://live.douyin.com/999', status='recording', anchor='测试主播', platform='抖音直播')
ts = state.get_tasks()
check('state 任务注册', len(ts) == 1 and ts[0]['status'] == 'recording')
state.add_log('hello webui')
check('state 日志', len(state.get_logs()) >= 1)

print()
if failures:
    print(f'❌ {len(failures)} 项失败: {failures}')
    sys.exit(1)
print('✅ 全部自测通过')
