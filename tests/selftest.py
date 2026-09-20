# -*- encoding: utf-8 -*-
"""重构自测：验证适配器注册表 / 配置加载 / URL 解析 / WebUI 应用。"""
import contextlib
import io
import os
import shutil
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
check('add 合法平台', ok == 'ok')
check('add 重复拒绝', store.add('https://www.douyu.com/123') == 'duplicate')
ok = store.add('https://www.huya.com/123', '高清', '虎牙测试')
check('add 虎牙合法', ok == 'ok')
check('add 带 query 重复拒绝', store.add('https://www.huya.com/123?foo=bar') == 'duplicate')
check('add 大小写不同重复拒绝', store.add('https://WWW.HUYA.com/123') == 'duplicate')
store.remove('https://www.huya.com/123')  # 清理虎牙条目，不影响后续用例
check('add 暂停任务重复拒绝',
      store.set_commented('https://www.douyu.com/123', True)
      and store.add('https://www.douyu.com/123') == 'duplicate'
      and store.set_commented('https://www.douyu.com/123', False))
ok = store.add('https://bad.unknown/1')
check('add 非法平台拒绝', ok == 'invalid')
check('parse_entry 多余逗号容错',
      parse_entry('原画,原画,https://live.douyin.com/1,主播: 测试').url == 'https://live.douyin.com/1')
check('add 整行粘贴识别为重复',
      store.add('原画,https://www.douyu.com/123,主播: 测试') == 'duplicate')

removed = store.remove('https://www.douyu.com/123')
check('remove 返回被删 URL', removed == ['https://www.douyu.com/123'])
check('remove 未命中返回空', store.remove('https://www.douyu.com/123') == [])
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
check('WebUI 含任务搜索与连接状态',
      'id="task-search"' in r.text and 'id="connection"' in r.text)
check('WebUI 操作不使用内联事件', 'onclick=' not in r.text and 'onchange=' not in r.text)
r = client.get('/sw.js')
check('Service Worker 不缓存配置与日志',
      r.status_code == 200 and "path === '/api/config'" in r.text and "path.startsWith('/api/logs')" in r.text)
r = client.post('/api/tasks', json={'url': 'https://live.douyin.com/999', 'quality': '超清', 'name': 'webui测试'})
check('POST /api/tasks', r.status_code == 200)
r = client.post('/api/tasks', json={'url': 'https://bad.unknown/1'})
check('POST 非法 URL 400', r.status_code == 400)
# 选平台 + 输 ID（21593109 已在种子文件中，应返回 409）
r = client.post('/api/tasks/from-id', json={'platform': 'B站直播', 'id': '21593109', 'quality': '高清', 'name': 'ID快捷'})
check('POST from-id 重复 409', r.status_code == 409)
r = client.post('/api/tasks/from-id', json={'platform': 'B站直播', 'id': '21593110', 'quality': '高清', 'name': 'ID快捷'})
check('POST from-id 成功', r.status_code == 200 and 'https://live.bilibili.com/21593110' in r.json().get('url', ''))
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

# 删除任务 → 停止请求
check('stop_requested 初始为 False', not state.stop_requested('https://live.douyin.com/999'))
r = client.delete('/api/tasks?url=https%3A%2F%2Flive.douyin.com%2F999')
check('DELETE 任务', r.status_code == 200)
check('DELETE 触发停止请求', state.stop_requested('https://live.douyin.com/999'))
state.clear_stop('https://live.douyin.com/999')
check('clear_stop 消费请求', not state.stop_requested('https://live.douyin.com/999'))

# ---------- 5. 部署校验（安装清单） ----------
print('[5] 部署校验')
from src import deploy_check

dep_dir = tempfile.mkdtemp()
os.makedirs(os.path.join(dep_dir, 'src', '__pycache__'))
os.makedirs(os.path.join(dep_dir, 'config'))
os.makedirs(os.path.join(dep_dir, 'logs'))
os.makedirs(os.path.join(dep_dir, 'webui', 'static'))
write_text = lambda rel, text: open(os.path.join(dep_dir, rel), 'w', encoding='utf-8').write(text)
write_text('main.py', 'print(1)\n')
write_text('src/url_config.py', 'def remove(url): return True\n')
write_text('webui/static/index.html', '<html></html>\n')
write_text('src/__pycache__/x.pyc', 'x')          # 字节码不参与
write_text('config/URL_config.ini', '原画,https://live.douyin.com/1\n')  # 运行数据不参与
write_text('logs/run.log', 'log\n')               # 运行日志不参与

files = deploy_check.iter_code_files(dep_dir)
check('清单只收录代码文件', files == ['main.py', 'src/url_config.py', 'webui/static/index.html'],
      f'实际 {files}')

info = deploy_check.make_manifest(dep_dir, source='/tmp/src', meta={'commit': 'abc123', 'branch': 'main'})
check('清单记录来源提交', info['file_count'] == 3 and info['commit'] == 'abc123')
check('一致时校验通过', deploy_check.verify(dep_dir)['state'] == 'ok')

# 跨版本混装：只有一侧文件被替换（线上“删除任务后仍继续录制”的成因）
write_text('src/url_config.py', 'def remove(url): return []\n')
skew = deploy_check.verify(dep_dir)
check('检出跨版本混装', skew['state'] == 'skew'
      and [i['path'] for i in skew['mismatched']] == ['src/url_config.py'])
check('告警文案指明重新部署', 'install.sh' in deploy_check.report(skew))

os.remove(os.path.join(dep_dir, 'main.py'))
check('检出文件缺失', deploy_check.verify(dep_dir)['missing'] == ['main.py'])
write_text('main.py', 'print(1)\n')
write_text('src/url_config.py', 'def remove(url): return True\n')
write_text('webui/static/app.py', '# 上次安装残留\n')
extra = deploy_check.verify(dep_dir)
check('检出清单外残留文件', extra['state'] == 'extra' and extra['extra'] == ['webui/static/app.py'])
check('无清单时跳过校验', deploy_check.verify(tempfile.mkdtemp())['state'] == 'no_manifest')

# 运维自己放进安装目录的东西：不参与校验，也不能被安装脚本删掉
os.makedirs(os.path.join(dep_dir, 'mybackup'), exist_ok=True)
write_text('mybackup/keep.txt', 'op data\n')
os.remove(os.path.join(dep_dir, 'webui', 'static', 'app.py'))
write_text('main.py', 'print(2)\n')                     # 模拟源码更新
info2 = deploy_check.make_manifest(dep_dir, source='/tmp/src')
check('清单不含运维自建目录', all(not p.startswith('mybackup/') for p in info2['files']),
      f'实际 {sorted(info2["files"])}')
remove, preserved = deploy_check.prune_candidates(dep_dir, '/tmp/src')
check('清理列表含旧代码', 'main.py' in remove and 'src' in remove, f'实际 {remove}')
check('运维自建目录不被清理', preserved == ['mybackup'], f'实际 {preserved}')
check('运维目录内容变动不影响校验', deploy_check.verify(dep_dir)['state'] == 'ok')

# 清单被误删：装过的目录必须告警，而不是静默跳过校验
marker = os.path.join(dep_dir, deploy_check.SENTINEL_NAME)
open(marker, 'w', encoding='utf-8').write('2026-09-20 04:00:00\n')
os.remove(os.path.join(dep_dir, deploy_check.MANIFEST_NAME))
lost = deploy_check.verify(dep_dir)
check('清单丢失后按“无法校验”告警', lost['state'] == 'unverified'
      and 'deploy/install.sh' in deploy_check.report(lost), f'实际 {lost["state"]}')
os.remove(marker)
check('未安装过的目录仍跳过校验', deploy_check.verify(dep_dir)['state'] == 'no_manifest')

# 清单取自源码：安装目录多出的文件必须报 extra，不能被"洗白"成已安装内容
src_dir = tempfile.mkdtemp()
inst_dir = tempfile.mkdtemp()
for d, rels in ((src_dir, ('main.py', 'src/url_config.py')), (inst_dir, ('main.py', 'src/url_config.py'))):
    for rel in rels:
        full = os.path.join(d, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, 'w', encoding='utf-8').write(rel + '\n')
open(os.path.join(inst_dir, 'src', 'legacy.py'), 'w', encoding='utf-8').write('# 清理残留\n')
deploy_check.make_manifest(inst_dir, source=src_dir, meta={'commit': 'c0ffee'})
residue = deploy_check.verify(inst_dir)
check('清单按源码生成，残留文件暴露为 extra',
      residue['state'] == 'extra' and residue['extra'] == ['src/legacy.py'],
      f'实际 {residue["state"]} {residue["extra"]}')
open(os.path.join(inst_dir, 'main.py'), 'w', encoding='utf-8').write('被改过的 main.py\n')
check('安装目录内容与源码不符报 skew', deploy_check.verify(inst_dir)['state'] == 'skew')
check('清单记录项目条目', deploy_check.load_manifest(inst_dir).get('entries') == ['main.py', 'src'],
      f'实际 {deploy_check.load_manifest(inst_dir).get("entries")}')

# 源码目录消失后校验范围不能漂移（否则运维自建目录会被误报为清单外文件）
os.remove(os.path.join(inst_dir, 'src', 'legacy.py'))     # 清掉上一步的残留
open(os.path.join(inst_dir, 'main.py'), 'w', encoding='utf-8').write('main.py\n')  # 还原上一步的篡改
os.makedirs(os.path.join(inst_dir, 'mybackup'), exist_ok=True)
open(os.path.join(inst_dir, 'mybackup', 'keep.txt'), 'w', encoding='utf-8').write('op\n')
deploy_check.make_manifest(inst_dir, source=src_dir)
check('清理残留后校验通过', deploy_check.verify(inst_dir)['state'] == 'ok')
shutil.rmtree(src_dir)
gone = deploy_check.verify(inst_dir)
check('源码目录消失后不误报清单外文件', gone['state'] == 'ok', f'实际 {gone["state"]} {gone["extra"]}')

# 名字不安全的条目（含换行等）必须让安装中止，而不是把拆分后的片段拼进删除目标
check('名字安全判定', deploy_check.is_safe_component('webui') and not deploy_check.is_safe_component('a\nb')
      and not deploy_check.is_safe_component('..') and not deploy_check.is_safe_component('a/b')
      and not deploy_check.is_safe_component(''))
bad_dir = tempfile.mkdtemp()
open(os.path.join(bad_dir, 'evil\nname'), 'w', encoding='utf-8').write('x')
open(os.path.join(bad_dir, 'config'), 'w', encoding='utf-8').write('keep\n')
check('检出名字不安全的条目', deploy_check.unsafe_entries(bad_dir) == ['evil\nname'])
remove_bad, preserve_bad = deploy_check.prune_candidates(bad_dir, dep_dir)
check('不安全名字不进入删除列表', 'evil\nname' not in remove_bad and 'config' not in remove_bad,
      f'实际 remove={remove_bad}')
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    unsafe_rc = deploy_check.main(['prune', '--dir', bad_dir])
    ok_rc = deploy_check.main(['prune', '--dir', dep_dir, '--source', dep_dir])
check('prune 对不安全名字返回非 0', unsafe_rc == 3, f'实际 {unsafe_rc}')
check('prune 正常目录返回 0', ok_rc == 0, f'实际 {ok_rc}')

# 运维放在安装目录里的文件不能算「清单外文件」：否则重跑安装也无法消除，
# 而安装脚本曾把该状态当成致命错误（代码已替换、服务却没重启）
op_dir = tempfile.mkdtemp()
os.makedirs(os.path.join(op_dir, 'src'))
for rel, text in (('main.py', 'main\n'), ('src/a.py', 'a\n')):
    open(os.path.join(op_dir, rel), 'w', encoding='utf-8').write(text)
deploy_check.make_manifest(op_dir, source=op_dir, meta={'commit': 'c1'})
open(os.path.join(op_dir, '.env'), 'w', encoding='utf-8').write('SECRET=1\n')
open(os.path.join(op_dir, 'start.sh'), 'w', encoding='utf-8').write('#!/bin/sh\n')
check('安装目录根部的运维文件不算清单外文件',
      deploy_check.verify(op_dir)['state'] == 'ok',
      f'实际 {deploy_check.verify(op_dir)}')

# 但项目目录内部的非本项目文件仍应报出来（升级会替换整个项目目录）
open(os.path.join(op_dir, 'src', 'operator.conf'), 'w', encoding='utf-8').write('op\n')
adv = deploy_check.verify(op_dir)
check('项目目录内的额外文件报 extra 且为提示性质', adv['state'] == 'extra'
      and adv['extra'] == ['src/operator.conf'], f'实际 {adv["state"]} {adv["extra"]}')
with contextlib.redirect_stdout(io.StringIO()):
    relax_rc = deploy_check.main(['verify', '--dir', op_dir])
    strict_rc = deploy_check.main(['verify', '--dir', op_dir, '--strict'])
check('extra 默认退出码为提示码', relax_rc == deploy_check.EXTRA_EXIT_CODE, f'实际 {relax_rc}')
check('extra --strict 视为失败', strict_rc == 1, f'实际 {strict_rc}')
check('升级前列出会被删除的运维文件',
      deploy_check.files_lost_on_copy(op_dir, src_dir) == ['src/operator.conf'],
      f'实际 {deploy_check.files_lost_on_copy(op_dir, src_dir)}')
check('源码中存在的文件不算会被删除',
      deploy_check.files_lost_on_copy(op_dir, op_dir) == [],
      f'实际 {deploy_check.files_lost_on_copy(op_dir, op_dir)}')
check('运行数据与根部运维文件不在会被删除之列',
      'config/URL_config.ini' not in deploy_check.files_lost_on_copy(dep_dir, dep_dir)
      and '.env' not in deploy_check.files_lost_on_copy(op_dir, src_dir))

r = client.get('/api/status')
check('WebUI 状态含部署字段', r.json().get('deploy', {}).get('state') == 'no_manifest',
      f'实际 {r.json().get("deploy")}')

print()
if failures:
    print(f'❌ {len(failures)} 项失败: {failures}')
    sys.exit(1)
print('✅ 全部自测通过')
