# -*- encoding: utf-8 -*-
"""
平台适配器插件系统
====================

将原先 main.py 中 `start_record()` 里上千行的 if/elif 平台分支链，
重构为统一的「适配器 + 注册表」模型：

    1. 每个平台实现一个 PlatformAdapter 子类，声明：
       - name        : 平台显示名（如 "抖音直播"）
       - hosts       : 域名匹配列表
       - patterns    : URL 子串匹配列表（兜底）
       - overseas    : 是否海外平台（请求/录制走代理、放宽 ffmpeg 超时）
       - force_proxy : 无代理时是否直接报错（海外平台一般为 True）
       - only_flv    : 强制使用 FLV 录制
       - only_audio  : 只录制音频
       - http_force  : 录制时强制 http（部分平台 https 拉流失败）
       - clean_url   : 解析 URL 时是否清理 query 参数
       - flv_preferred: 录制时优先使用 flv 源
       - record_headers: 录制时附加的 HTTP header（str 或 callable）

    2. 实现 `resolve(url, ctx) -> dict | None`，返回统一的直播信息 dict：
       {
           "anchor_name": str,
           "is_live": bool,
           "title": str,
           "quality": str,
           "m3u8_url": str,
           "flv_url": str,
           "record_url": str,
           # 可选扩展: new_cookies / new_token / uid 等
       }
       返回 None 表示本次解析失败（网络/无代理等），调用方应跳过本轮。

    3. 通过 `@register_adapter` 自动注册到全局 registry，
       新增平台无需改动 main.py 与 WebUI。

新增平台三步走：
    1. 在 src/spider.py 中实现抓取函数（返回原始 json_data）；
    2. 在 src/stream.py 中实现流地址解析函数（返回统一 dict）；
    3. 在本文件中写一个适配器类并 @register_adapter。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from src import spider, stream
from src.utils import logger

# ---------------------------------------------------------------------------
# 解析上下文
# ---------------------------------------------------------------------------


@dataclass
class ResolveContext:
    """适配器解析上下文：承载所有运行配置，避免适配器与 main.py 强耦合。"""

    quality: str = 'OD'                        # 画质代码 OD/BD/UHD/HD/SD/LD
    proxy: Optional[str] = None                # 实际生效的代理地址（已按平台过滤）
    global_proxy: bool = False                 # 是否检测到全局/系统代理
    cookies: dict = field(default_factory=dict)   # {cookie_key: cookie_str}
    accounts: dict = field(default_factory=dict)  # {账号密码key: value}
    tokens: dict = field(default_factory=dict)    # Authorization 令牌

    def cookie(self, key: str) -> str:
        """读取指定 key 的 cookie，不存在时返回空串。"""
        return self.cookies.get(key, '') or ''

    def account(self, key: str) -> str:
        return self.accounts.get(key, '') or ''

    def token(self, key: str) -> str:
        return self.tokens.get(key, '') or ''


# ---------------------------------------------------------------------------
# 适配器基类
# ---------------------------------------------------------------------------


class PlatformAdapter:
    """平台适配器基类。子类必须实现 resolve()。"""

    name: str = ''
    hosts: tuple = ()
    patterns: tuple = ()
    overseas: bool = False
    force_proxy: bool = False
    only_flv: bool = False
    only_audio: bool = False
    http_force: bool = False
    clean_url: bool = False
    flv_preferred: bool = False
    record_headers: Optional[str | Callable[[str], Optional[str]]] = None
    # WebUI 快捷添加：由平台 ID/用户名构造完整直播间 URL
    url_template: str = ''        # 如 'https://live.bilibili.com/{id}'，不支持 ID 模式留空
    id_placeholder: str = ''      # 输入框提示，如 '直播间ID，如 21593109' / '用户名，如 pearlgaga88'

    def matches(self, url: str) -> bool:
        return any(h in url for h in self.hosts) or any(p in url for p in self.patterns)

    def build_url(self, identifier: str) -> str:
        """由平台 ID/用户名构造完整直播间 URL；不支持 ID 模式返回空串。"""
        if not self.url_template:
            return ''
        identifier = (identifier or '').strip()
        if not identifier:
            return ''
        # 统一去掉 @ 前缀（TikTok / YouTube 等用户名式 ID）
        identifier = identifier.lstrip('@')
        url = self.url_template.format(id=identifier)
        return url if self.matches(url) else ''

    def check_proxy(self, ctx: ResolveContext) -> bool:
        """海外平台强制代理检查；不满足时打日志并返回 False。"""
        if self.force_proxy and not (ctx.global_proxy or ctx.proxy):
            logger.error(f"错误信息: 网络异常，请检查本网络是否能正常访问{self.name}平台")
            return False
        return True

    def get_headers(self, live_url: str) -> Optional[str]:
        """录制时附加 header。支持静态字符串或 callable（如 shopee 动态 origin）。"""
        h = self.record_headers
        if h is None:
            return None
        if isinstance(h, str):
            return h
        return h(live_url)

    async def resolve(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        raise NotImplementedError


class TwoStepAdapter(PlatformAdapter):
    """两段式适配器：spider 抓取 json_data → stream 解析流地址。"""

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        raise NotImplementedError

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        raise NotImplementedError

    async def resolve(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        if not self.check_proxy(ctx):
            return None
        data = await self.fetch(url, ctx)
        if not data:
            return None
        return await self.build(data, ctx)


class DirectAdapter(PlatformAdapter):
    """一段式适配器：spider 直接返回统一格式 dict。"""

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        raise NotImplementedError

    async def resolve(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        if not self.check_proxy(ctx):
            return None
        return await self.fetch(url, ctx)


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


class AdapterRegistry:
    """适配器注册表：按注册顺序匹配 URL。"""

    def __init__(self) -> None:
        self._adapters: list[PlatformAdapter] = []

    def register(self, adapter: PlatformAdapter) -> None:
        self._adapters.append(adapter)

    def match(self, url: str) -> Optional[PlatformAdapter]:
        for adapter in self._adapters:
            if adapter.matches(url):
                return adapter
        return None

    def all(self) -> list[PlatformAdapter]:
        return list(self._adapters)

    def hosts(self) -> list[str]:
        """所有已注册的 host/pattern，供 URL 配置校验使用。"""
        result: list[str] = []
        for adapter in self._adapters:
            result.extend(adapter.hosts)
            result.extend(adapter.patterns)
        return result

    def overseas_hosts(self) -> list[str]:
        result: list[str] = []
        for adapter in self._adapters:
            if adapter.overseas:
                result.extend(adapter.hosts)
                result.extend(adapter.patterns)
        return result

    def platform_names(self) -> list[str]:
        return [ad.name for ad in self._adapters]


registry = AdapterRegistry()


def register_adapter(adapter: PlatformAdapter) -> PlatformAdapter:
    """类装饰器/直接调用：注册适配器（类自动实例化为单例）。"""
    if isinstance(adapter, type):
        adapter = adapter()
    registry.register(adapter)
    return adapter


# ---------------------------------------------------------------------------
# 平台适配器（按 start_record 原逻辑逐平台移植）
# ---------------------------------------------------------------------------


@register_adapter
class DouyinAdapter(TwoStepAdapter):
    name = '抖音直播'
    hosts = ('live.douyin.com', 'v.douyin.com', 'www.douyin.com')
    clean_url = True
    flv_preferred = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        if 'v.douyin.com' not in url and '/user/' not in url:
            return await spider.get_douyin_web_stream_data(
                url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('dy'))
        return await spider.get_douyin_app_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('dy'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_douyin_stream_url(data, ctx.quality, ctx.proxy)


@register_adapter
class TikTokAdapter(TwoStepAdapter):
    name = 'TikTok直播'
    hosts = ('www.tiktok.com',)
    overseas = True
    force_proxy = True
    flv_preferred = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_tiktok_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('tiktok'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_tiktok_stream_url(data, ctx.quality, ctx.proxy)


@register_adapter
class KuaishouAdapter(TwoStepAdapter):
    name = '快手直播'
    hosts = ('live.kuaishou.com',)

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_kuaishou_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('ks'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_kuaishou_stream_url(data, ctx.quality)


@register_adapter
class HuyaAdapter(PlatformAdapter):
    """虎牙：原画/蓝光/超清走 app 接口，其余画质走 web 接口。"""
    name = '虎牙直播'
    hosts = ('www.huya.com',)
    clean_url = True

    async def resolve(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        if ctx.quality in ('OD', 'BD', 'UHD'):
            return await spider.get_huya_app_stream_url(
                url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('hy'))
        json_data = await spider.get_huya_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('hy'))
        if not json_data:
            return None
        return await stream.get_huya_stream_url(json_data, ctx.quality)


@register_adapter
class DouyuAdapter(TwoStepAdapter):
    name = '斗鱼直播'
    hosts = ('www.douyu.com',)

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_douyu_info_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('douyu'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_douyu_stream_url(
            data, ctx.quality, ctx.cookie('douyu'), ctx.proxy)


@register_adapter
class YYAdapter(TwoStepAdapter):
    name = 'YY直播'
    hosts = ('www.yy.com',)

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_yy_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('yy'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_yy_stream_url(data)


@register_adapter
class BilibiliAdapter(TwoStepAdapter):
    name = 'B站直播'
    hosts = ('live.bilibili.com',)
    clean_url = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_bilibili_room_info(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('bili'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_bilibili_stream_url(
            data, ctx.quality, ctx.proxy, ctx.cookie('bili'))


@register_adapter
class XiaohongshuAdapter(DirectAdapter):
    name = '小红书直播'
    hosts = ('www.xiaohongshu.com', 'xhslink.com', 'www.redelight.cn')

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_xhs_stream_url(
            url, proxy_addr=ctx.proxy, cookies=ctx.cookie('xhs'))


@register_adapter
class BigoAdapter(DirectAdapter):
    name = 'Bigo直播'
    hosts = ('www.bigo.tv', 'slink.bigovideo.tv')

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_bigo_stream_url(
            url, proxy_addr=ctx.proxy, cookies=ctx.cookie('bigo'))


@register_adapter
class BluedAdapter(DirectAdapter):
    name = 'Blued直播'
    hosts = ('app.blued.cn',)
    record_headers = 'referer:https://app.blued.cn'

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_blued_stream_url(
            url, proxy_addr=ctx.proxy, cookies=ctx.cookie('blued'))


@register_adapter
class SoopAdapter(TwoStepAdapter):
    name = 'SOOP'
    hosts = ('sooplive.co.kr', 'sooplive.com')
    overseas = True
    force_proxy = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_sooplive_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('sooplive'),
            username=ctx.account('sooplive_username'), password=ctx.account('sooplive_password'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_stream_url(data, ctx.quality, spec=True)


@register_adapter
class NeteaseAdapter(TwoStepAdapter):
    name = '网易CC直播'
    hosts = ('cc.163.com',)

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_netease_stream_data(url=url, cookies=ctx.cookie('netease'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_netease_stream_url(data, ctx.quality)


@register_adapter
class QiandureboAdapter(DirectAdapter):
    name = '千度热播'
    hosts = ('qiandurebo.com',)
    record_headers = 'referer:https://qiandurebo.com'

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_qiandurebo_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('qiandurebo'))


@register_adapter
class PandaTVAdapter(TwoStepAdapter):
    name = 'PandaTV'
    hosts = ('www.pandalive.co.kr',)
    overseas = True
    force_proxy = True
    record_headers = 'origin:https://www.pandalive.co.kr'

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_pandatv_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('pandatv'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_stream_url(data, ctx.quality, spec=True)


@register_adapter
class MaoerFMAdapter(DirectAdapter):
    name = '猫耳FM直播'
    hosts = ('fm.missevan.com',)
    only_audio = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_maoerfm_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('maoerfm'))


@register_adapter
class WinkTVAdapter(TwoStepAdapter):
    name = 'WinkTV'
    hosts = ('www.winktv.co.kr',)
    overseas = True
    force_proxy = True
    record_headers = 'origin:https://www.winktv.co.kr'

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_winktv_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('winktv'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_stream_url(data, ctx.quality, spec=True)


@register_adapter
class FlexTVAdapter(PlatformAdapter):
    """FlexTV：解析结果中可能带 play_url_list（走通用解析）或已是最终结果。"""
    name = 'FlexTV'
    hosts = ('www.flextv.co.kr', 'www.ttinglive.com')
    overseas = True
    force_proxy = True
    record_headers = 'origin:https://www.flextv.co.kr'

    async def resolve(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        if not self.check_proxy(ctx):
            return None
        json_data = await spider.get_flextv_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('flextv'),
            username=ctx.account('flextv_username'), password=ctx.account('flextv_password'))
        if not json_data:
            return None
        if 'play_url_list' in json_data:
            return await stream.get_stream_url(json_data, ctx.quality, spec=True)
        return json_data


@register_adapter
class LookAdapter(DirectAdapter):
    name = 'Look直播'
    hosts = ('look.163.com',)
    only_audio = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_looklive_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('look'))


@register_adapter
class PopkonTVAdapter(DirectAdapter):
    name = 'PopkonTV'
    hosts = ('www.popkontv.com',)
    overseas = True
    force_proxy = True
    record_headers = 'origin:https://www.popkontv.com'

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_popkontv_stream_url(
            url=url, proxy_addr=ctx.proxy,
            access_token=ctx.token('popkontv_token'),
            username=ctx.account('popkontv_username'),
            password=ctx.account('popkontv_password'),
            partner_code=ctx.account('popkontv_partner_code'))


@register_adapter
class TwitCastingAdapter(TwoStepAdapter):
    name = 'TwitCasting'
    hosts = ('twitcasting.tv',)

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_twitcasting_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('twitcasting'),
            account_type=ctx.account('twitcasting_account_type'),
            username=ctx.account('twitcasting_username'),
            password=ctx.account('twitcasting_password'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_stream_url(data, ctx.quality, spec=False)


@register_adapter
class BaiduAdapter(TwoStepAdapter):
    name = '百度直播'
    hosts = ('live.baidu.com',)

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_baidu_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('baidu'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_stream_url(data, ctx.quality)


@register_adapter
class WeiboAdapter(TwoStepAdapter):
    name = '微博直播'
    hosts = ('weibo.com',)

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_weibo_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('weibo'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_stream_url(data, ctx.quality, hls_extra_key='m3u8_url')


@register_adapter
class KugouAdapter(DirectAdapter):
    name = '酷狗直播'
    hosts = ('fanxing.kugou.com', 'fanxing2.kugou.com', 'mfanxing.kugou.com')

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_kugou_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('kugou'))


@register_adapter
class TwitchTVAdapter(TwoStepAdapter):
    name = 'TwitchTV'
    hosts = ('www.twitch.tv',)
    overseas = True
    force_proxy = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_twitchtv_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('twitch'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_stream_url(data, ctx.quality, spec=True)


@register_adapter
class LiveMeAdapter(DirectAdapter):
    name = 'LiveMe'
    hosts = ('www.liveme.com',)
    overseas = True
    force_proxy = True
    clean_url = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_liveme_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('liveme'))


@register_adapter
class HuajiaoAdapter(DirectAdapter):
    name = '花椒直播'
    hosts = ('www.huajiao.com',)
    clean_url = True
    only_flv = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_huajiao_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('huajiao'))


@register_adapter
class LiuxingAdapter(DirectAdapter):
    name = '流星直播'
    hosts = ('www.7u66.com', 'wap.7u66.com')

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_liuxing_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('liuxing'))


@register_adapter
class ShowRoomAdapter(TwoStepAdapter):
    name = 'ShowRoom'
    hosts = ('www.showroom-live.com',)
    overseas = True
    force_proxy = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_showroom_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('showroom'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_stream_url(data, ctx.quality, spec=True)


@register_adapter
class AcfunAdapter(TwoStepAdapter):
    name = 'Acfun'
    hosts = ('live.acfun.cn', 'm.acfun.cn')

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_acfun_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('acfun'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_stream_url(data, ctx.quality, url_type='flv', flv_extra_key='url')


@register_adapter
class ChangliaoAdapter(DirectAdapter):
    name = '畅聊直播'
    hosts = ('live.tlclw.com', 'wap.tlclw.com')

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_changliao_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('changliao'))


@register_adapter
class YinboAdapter(DirectAdapter):
    name = '音播直播'
    hosts = ('live.ybw1666.com', 'wap.ybw1666.com')

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_yinbo_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('yinbo'))


@register_adapter
class YingkeAdapter(DirectAdapter):
    name = '映客直播'
    hosts = ('www.inke.cn',)

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_yingke_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('yingke'))


@register_adapter
class ZhihuAdapter(DirectAdapter):
    name = '知乎直播'
    hosts = ('www.zhihu.com',)
    clean_url = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_zhihu_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('zhihu'))


@register_adapter
class CHZZKAdapter(TwoStepAdapter):
    name = 'CHZZK'
    hosts = ('chzzk.naver.com', 'm.chzzk.naver.com')
    overseas = True
    force_proxy = True
    clean_url = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_chzzk_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('chzzk'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_stream_url(data, ctx.quality, spec=True)


@register_adapter
class HaixiuAdapter(DirectAdapter):
    name = '嗨秀直播'
    hosts = ('www.haixiutv.com',)
    clean_url = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_haixiu_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('haixiu'))


@register_adapter
class VVXqiuAdapter(DirectAdapter):
    name = 'VV星球'
    hosts = ('h5webcdnp.vvxqiu.com',)

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_vvxqiu_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('vvxqiu'))


@register_adapter
class YiqiliveAdapter(DirectAdapter):
    name = '17Live'
    hosts = ('17.live',)
    record_headers = 'referer:https://17.live/en/live/6302408'

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_17live_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('yiqilive'))


@register_adapter
class LangliveAdapter(DirectAdapter):
    name = '浪Live'
    hosts = ('www.lang.live',)
    record_headers = 'referer:https://www.lang.live'

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_langlive_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('langlive'))


@register_adapter
class PPLiveAdapter(DirectAdapter):
    name = '漂漂直播'
    hosts = ('m.pp.weimipopo.com',)

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_pplive_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('pplive'))


@register_adapter
class SixRoomAdapter(DirectAdapter):
    name = '六间房直播'
    hosts = ('v.6.cn', 'm.6.cn')
    clean_url = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_6room_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('six_room'))


@register_adapter
class LehaitvAdapter(DirectAdapter):
    """乐嗨直播：复用嗨秀的抓取逻辑（与原 main.py 一致）。"""
    name = '乐嗨直播'
    hosts = ('www.lehaitv.com',)
    clean_url = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_haixiu_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('lehaitv'))


@register_adapter
class HuamaoAdapter(DirectAdapter):
    """花猫直播：复用漂漂的抓取逻辑（与原 main.py 一致）。"""
    name = '花猫直播'
    hosts = ('h.catshow168.com',)

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_pplive_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('huamao'))


@register_adapter
class ShopeeAdapter(DirectAdapter):
    name = 'shopee'
    hosts = ('live.shopee', '.shp.ee')
    overseas = True
    only_flv = True
    http_force = True
    record_headers = staticmethod(lambda live_url: f'origin:{"/".join(live_url.split("/")[0:3])}')

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_shopee_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('shopee'))


@register_adapter
class YoutubeAdapter(TwoStepAdapter):
    name = 'Youtube'
    hosts = ('www.youtube.com', 'youtu.be')
    overseas = True
    force_proxy = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_youtube_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('youtube'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_stream_url(data, ctx.quality, spec=True)


@register_adapter
class TaobaoAdapter(TwoStepAdapter):
    name = '淘宝直播'
    hosts = ('e.tb.cn', 'huodong.m.taobao.com')

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_taobao_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('taobao'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_stream_url(
            data, ctx.quality, url_type='all', hls_extra_key='hlsUrl', flv_extra_key='flvUrl')


@register_adapter
class JDAdapter(DirectAdapter):
    name = '京东直播'
    hosts = ('3.cn', 'eco.m.jd.com')

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_jd_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('jd'))


@register_adapter
class FaceitAdapter(TwoStepAdapter):
    name = 'faceit'
    hosts = ('www.faceit.com', 'faceit.com')
    overseas = True
    force_proxy = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_faceit_stream_data(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('faceit'))

    async def build(self, data: dict, ctx: ResolveContext) -> Optional[dict]:
        return await stream.get_stream_url(data, ctx.quality, spec=True)


@register_adapter
class MiguAdapter(DirectAdapter):
    name = '咪咕直播'
    hosts = ('www.miguvideo.com', 'm.miguvideo.com')
    http_force = True

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_migu_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('migu'))


@register_adapter
class LianjieAdapter(DirectAdapter):
    name = '连接直播'
    hosts = ('show.lailianjie.com',)

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_lianjie_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('lianjie'))


@register_adapter
class LaixiuAdapter(DirectAdapter):
    name = '来秀直播'
    hosts = ('www.imkktv.com',)

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_laixiu_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('laixiu'))


@register_adapter
class PicartoAdapter(DirectAdapter):
    name = 'Picarto'
    hosts = ('www.picarto.tv',)

    async def fetch(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        return await spider.get_picarto_stream_url(
            url=url, proxy_addr=ctx.proxy, cookies=ctx.cookie('picarto'))


@register_adapter
class CustomStreamAdapter(PlatformAdapter):
    """自定义 m3u8/flv 直播源（无平台归属）。"""
    name = '自定义录制直播'
    patterns = ('.m3u8', '.flv')

    async def resolve(self, url: str, ctx: ResolveContext) -> Optional[dict]:
        result = {
            "anchor_name": f'{self.name}_{uuid.uuid4().hex[:8]}',
            "is_live": True,
            "record_url": url,
        }
        if '.flv' in url:
            result['flv_url'] = url
        else:
            result['m3u8_url'] = url
        return result


# ---------------------------------------------------------------------------
# WebUI 快捷添加：平台 ID/用户名 → 直播间 URL 模板
# （未在此列表中的平台请在 WebUI 中粘贴完整网址）
# ---------------------------------------------------------------------------
_PLATFORM_URL_TEMPLATES = {
    '抖音直播': ('https://live.douyin.com/{id}', '直播间ID，如 745964462470'),
    'B站直播': ('https://live.bilibili.com/{id}', '直播间ID，如 21593109'),
    '虎牙直播': ('https://www.huya.com/{id}', '房间号，如 116'),
    '斗鱼直播': ('https://www.douyu.com/{id}', '房间号，如 4921614'),
    '快手直播': ('https://live.kuaishou.com/u/{id}', '快手号/用户名，如 yall1102'),
    'TikTok直播': ('https://www.tiktok.com/@{id}/live', '用户名，如 pearlgaga88'),
    'Youtube': ('https://www.youtube.com/@{id}/live', '频道Handle，如 @example'),
    'TwitchTV': ('https://www.twitch.tv/{id}', '频道名，如 xqc'),
    'Acfun': ('https://live.acfun.cn/live/{id}', '直播间ID，如 21593109'),
    '网易CC直播': ('https://cc.163.com/{id}', '直播间ID'),
    '花椒直播': ('https://www.huajiao.com/l/{id}', '房间号'),
    '六间房直播': ('https://v.6.cn/{id}', '房间号'),
    '映客直播': ('https://www.inke.cn/live.html?uid={id}', '主播UID'),
    'CHZZK': ('https://chzzk.naver.com/live/{id}', '直播间ID'),
    '17Live': ('https://17.live/live/{id}', '直播间ID'),
    '浪Live': ('https://www.lang.live/live/{id}', '直播间ID'),
    '知乎直播': ('https://www.zhihu.com/lives/{id}', '直播ID'),
    'Bigo直播': ('https://www.bigo.tv/{id}', '主播ID'),
    '酷狗直播': ('https://fanxing.kugou.com/{id}', '房间号'),
    '猫耳FM直播': ('https://fm.missevan.com/live/{id}', '直播间ID'),
    'SOOP': ('https://www.sooplive.com/{id}', 'BJ号/ID'),
    'TwitCasting': ('https://twitcasting.tv/{id}', '用户ID'),
    'Picarto': ('https://www.picarto.tv/{id}', '频道名'),
    '流星直播': ('https://www.7u66.com/{id}', '房间号'),
    '小红书直播': ('https://www.xiaohongshu.com/user/profile/{id}', '用户ID'),
}
for _adapter in registry.all():
    _tpl = _PLATFORM_URL_TEMPLATES.get(_adapter.name)
    if _tpl:
        _adapter.url_template, _adapter.id_placeholder = _tpl


# ---------------------------------------------------------------------------
# 便捷入口
# ---------------------------------------------------------------------------


def match(url: str) -> Optional[PlatformAdapter]:
    """根据 URL 匹配平台适配器（未匹配返回 None）。"""
    return registry.match(url)
