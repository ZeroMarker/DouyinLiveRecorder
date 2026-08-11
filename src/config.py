# -*- encoding: utf-8 -*-
"""
配置加载模块
=============

集中管理 config/config.ini 的读取与类型转换，替代 main.py 中
逐项 `read_config_value` + 全局变量的做法。

- 保持 config.ini 键名/格式完全兼容（中文键、是/否布尔）
- 首次运行自动补全缺失配置项（写回默认值）
- 提供 AppConfig 结构化对象 + ResolveContext 供适配器使用
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from typing import Any, Optional

OPTIONS = {"是": True, "否": False}

CONFIG_SECTIONS = ('录制设置', '推送配置', 'Cookie', 'Authorization', '账号密码')

# config.ini Cookie 键名 → 适配器 cookie key
COOKIE_INI_KEYS: dict[str, str] = {
    'dy': '抖音cookie',
    'ks': '快手cookie',
    'tiktok': 'tiktok_cookie',
    'hy': '虎牙cookie',
    'douyu': '斗鱼cookie',
    'yy': 'yy_cookie',
    'bili': 'B站cookie',
    'xhs': '小红书cookie',
    'bigo': 'bigo_cookie',
    'blued': 'blued_cookie',
    'sooplive': 'sooplive_cookie',
    'netease': 'netease_cookie',
    'qiandurebo': '千度热播_cookie',
    'pandatv': 'pandatv_cookie',
    'maoerfm': '猫耳fm_cookie',
    'winktv': 'winktv_cookie',
    'flextv': 'flextv_cookie',
    'look': 'look_cookie',
    'twitcasting': 'twitcasting_cookie',
    'baidu': 'baidu_cookie',
    'weibo': 'weibo_cookie',
    'kugou': 'kugou_cookie',
    'twitch': 'twitch_cookie',
    'liveme': 'liveme_cookie',
    'huajiao': 'huajiao_cookie',
    'liuxing': 'liuxing_cookie',
    'showroom': 'showroom_cookie',
    'acfun': 'acfun_cookie',
    'changliao': 'changliao_cookie',
    'yinbo': 'yinbo_cookie',
    'yingke': 'yingke_cookie',
    'zhihu': 'zhihu_cookie',
    'chzzk': 'chzzk_cookie',
    'haixiu': 'haixiu_cookie',
    'vvxqiu': 'vvxqiu_cookie',
    'yiqilive': '17live_cookie',
    'langlive': 'langlive_cookie',
    'pplive': 'pplive_cookie',
    'six_room': '6room_cookie',
    'lehaitv': 'lehaitv_cookie',
    'huamao': 'huamao_cookie',
    'shopee': 'shopee_cookie',
    'youtube': 'youtube_cookie',
    'taobao': 'taobao_cookie',
    'jd': 'jd_cookie',
    'faceit': 'faceit_cookie',
    'migu': 'migu_cookie',
    'lianjie': 'lianjie_cookie',
    'laixiu': 'laixiu_cookie',
    'picarto': 'picarto_cookie',
}

# 账号密码 / Authorization：适配器 key -> (section, ini 键, 默认值)
ACCOUNT_INI_KEYS: dict[str, tuple[str, str, str]] = {
    'sooplive_username': ('账号密码', 'sooplive账号', ''),
    'sooplive_password': ('账号密码', 'sooplive密码', ''),
    'flextv_username': ('账号密码', 'flextv账号', ''),
    'flextv_password': ('账号密码', 'flextv密码', ''),
    'popkontv_username': ('账号密码', 'popkontv账号', ''),
    'popkontv_partner_code': ('账号密码', 'partner_code', 'P-00001'),
    'popkontv_password': ('账号密码', 'popkontv密码', ''),
    'twitcasting_account_type': ('账号密码', 'twitcasting账号类型', 'normal'),
    'twitcasting_username': ('账号密码', 'twitcasting账号', ''),
    'twitcasting_password': ('账号密码', 'twitcasting密码', ''),
    'popkontv_token': ('Authorization', 'popkontv_token', ''),
}

# 推送配置：ini 键 -> (属性名, 默认值, 类型转换)
PUSH_INI_KEYS: dict[str, tuple[str, Any, Any]] = {
    '直播状态推送渠道': ('live_status_push', '', str),
    '钉钉推送接口链接': ('dingtalk_api_url', '', str),
    '微信推送接口链接': ('xizhi_api_url', '', str),
    'bark推送接口链接': ('bark_msg_api', '', str),
    'bark推送中断级别': ('bark_msg_level', 'active', str),
    'bark推送铃声': ('bark_msg_ring', 'bell', str),
    '钉钉通知@对象(填手机号)': ('dingtalk_phone_num', '', str),
    '钉钉通知@全体(是/否)': ('dingtalk_is_atall', False, 'bool'),
    'tgapi令牌': ('tg_token', '', str),
    'tg聊天id(个人或者群组id)': ('tg_chat_id', '', str),
    'SMTP邮件服务器': ('email_host', '', str),
    '是否使用SMTP服务SSL加密(是/否)': ('open_smtp_ssl', True, 'bool'),
    'SMTP邮件服务器端口': ('smtp_port', '', str),
    '邮箱登录账号': ('login_email', '', str),
    '发件人密码(授权码)': ('email_password', '', str),
    '发件人邮箱': ('sender_email', '', str),
    '发件人显示昵称': ('sender_name', '', str),
    '收件人邮箱': ('to_email', '', str),
    'ntfy推送地址': ('ntfy_api', '', str),
    'ntfy推送标签': ('ntfy_tags', 'tada', str),
    'ntfy推送邮箱': ('ntfy_email', '', str),
    'pushplus推送token': ('pushplus_token', '', str),
    '自定义推送标题': ('push_message_title', '直播间状态更新通知', str),
    '自定义开播推送内容': ('begin_push_message_text', '', str),
    '自定义关播推送内容': ('over_push_message_text', '', str),
    '只推送通知不录制(是/否)': ('disable_record', False, 'bool'),
    '直播推送检测频率(秒)': ('push_check_seconds', 1800, int),
    '开播推送开启(是/否)': ('begin_show_push', True, 'bool'),
    '关播推送开启(是/否)': ('over_show_push', False, 'bool'),
}


@dataclass
class AppConfig:
    """结构化应用配置。"""

    # ---- 录制设置 ----
    language: str = 'zh_cn'
    skip_proxy_check: bool = False
    video_save_path: str = ''
    folder_by_author: bool = True
    folder_by_time: bool = False
    folder_by_title: bool = False
    filename_by_title: bool = False
    clean_emoji: bool = True
    video_save_type: str = 'TS'
    video_record_quality: str = '原画'
    use_proxy: bool = False
    proxy_addr: Optional[str] = None
    max_request: int = 3
    delay_default: int = 120
    local_delay_default: int = 0
    loop_time: bool = False
    show_url: bool = False
    split_video_by_time: bool = False
    enable_https_recording: bool = False
    disk_space_limit: float = 1.0
    split_time: str = '1800'
    converts_to_mp4: bool = False
    converts_to_h264: bool = False
    delete_origin_file: bool = False
    create_time_file: bool = False
    is_run_script: bool = False
    custom_script: Optional[str] = None
    enable_proxy_platform_list: Optional[list] = None
    extra_enable_proxy_platform_list: Optional[list] = None

    # ---- 推送配置（展开自 PUSH_INI_KEYS）----
    live_status_push: str = ''
    dingtalk_api_url: str = ''
    xizhi_api_url: str = ''
    bark_msg_api: str = ''
    bark_msg_level: str = 'active'
    bark_msg_ring: str = 'bell'
    dingtalk_phone_num: str = ''
    dingtalk_is_atall: bool = False
    tg_token: str = ''
    tg_chat_id: str = ''
    email_host: str = ''
    open_smtp_ssl: bool = True
    smtp_port: str = ''
    login_email: str = ''
    email_password: str = ''
    sender_email: str = ''
    sender_name: str = ''
    to_email: str = ''
    ntfy_api: str = ''
    ntfy_tags: str = 'tada'
    ntfy_email: str = ''
    pushplus_token: str = ''
    push_message_title: str = '直播间状态更新通知'
    begin_push_message_text: str = ''
    over_push_message_text: str = ''
    disable_record: bool = False
    push_check_seconds: int = 1800
    begin_show_push: bool = True
    over_show_push: bool = False

    # ---- Cookie / 账号 / 令牌 ----
    cookies: dict = field(default_factory=dict)
    accounts: dict = field(default_factory=dict)
    tokens: dict = field(default_factory=dict)


def _cast(value: str, type_hint: Any, default: Any) -> Any:
    if type_hint == 'bool':
        return OPTIONS.get(value, default)
    if type_hint is int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if type_hint is float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return value


def _read_value(parser: configparser.RawConfigParser, section: str, option: str,
                default: Any, encoding: str, file_path: str) -> str:
    """读取配置；缺失时写回默认值（保持原 read_config_value 行为）。"""
    try:
        parser.read(file_path, encoding=encoding)
        for sec in CONFIG_SECTIONS:
            if sec not in parser.sections():
                parser.add_section(sec)
        return parser.get(section, option)
    except (configparser.NoSectionError, configparser.NoOptionError):
        parser.set(section, option, str(default))
        with open(file_path, 'w', encoding=encoding) as f:
            parser.write(f)
        return str(default)


def load_config(config_file: str, encoding: str = 'utf-8-sig') -> AppConfig:
    """从 config.ini 加载完整配置。"""
    parser = configparser.RawConfigParser()
    cfg = AppConfig()

    # ---- 录制设置 ----
    RECORD_INI = [
        ('language', 'language(zh_cn/en)', 'zh_cn', str),
        ('skip_proxy_check', '是否跳过代理检测(是/否)', '否', 'bool'),
        ('video_save_path', '直播保存路径(不填则默认)', '', str),
        ('folder_by_author', '保存文件夹是否以作者区分', '是', 'bool'),
        ('folder_by_time', '保存文件夹是否以时间区分', '否', 'bool'),
        ('folder_by_title', '保存文件夹是否以标题区分', '否', 'bool'),
        ('filename_by_title', '保存文件名是否包含标题', '否', 'bool'),
        ('clean_emoji', '是否去除名称中的表情符号', '是', 'bool'),
        ('video_save_type', '视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频', 'ts', str),
        ('video_record_quality', '原画|超清|高清|标清|流畅', '原画', str),
        ('use_proxy', '是否使用代理ip(是/否)', '是', 'bool'),
        ('proxy_addr', '代理地址', '', str),
        ('max_request', '同一时间访问网络的线程数', 3, int),
        ('delay_default', '循环时间(秒)', 120, int),
        ('local_delay_default', '排队读取网址时间(秒)', 0, int),
        ('loop_time', '是否显示循环秒数', '否', 'bool'),
        ('show_url', '是否显示直播源地址', '否', 'bool'),
        ('split_video_by_time', '分段录制是否开启', '否', 'bool'),
        ('enable_https_recording', '是否强制启用https录制', '否', 'bool'),
        ('disk_space_limit', '录制空间剩余阈值(gb)', 1.0, float),
        ('split_time', '视频分段时间(秒)', 1800, int),
        ('converts_to_mp4', '录制完成后自动转为mp4格式', '否', 'bool'),
        ('converts_to_h264', 'mp4格式重新编码为h264', '否', 'bool'),
        ('delete_origin_file', '追加格式后删除原文件', '否', 'bool'),
        ('create_time_file', '生成时间字幕文件', '否', 'bool'),
        ('is_run_script', '是否录制完成后执行自定义脚本', '否', 'bool'),
        ('custom_script', '自定义脚本执行命令', '', str),
        ('enable_proxy_platform', '使用代理录制的平台(逗号分隔)',
         'tiktok, soop, pandalive, winktv, flextv, popkontv, twitch, liveme, showroom, chzzk, shopee, shp, youtu, faceit', str),
        ('extra_enable_proxy', '额外使用代理录制的平台(逗号分隔)', '', str),
    ]
    for attr, ini_key, default, type_hint in RECORD_INI:
        raw = _read_value(parser, '录制设置', ini_key, default, encoding, config_file)
        if attr == 'video_save_type':
            # 规范化保存格式
            value = str(raw).upper()
            if value not in ("FLV", "MKV", "TS", "MP4", "MP3音频", "M4A音频", "MP3", "M4A"):
                value = "TS"
            setattr(cfg, attr, value)
            continue
        if attr == 'custom_script':
            cfg.custom_script = str(raw) if _cast(raw, 'bool', False) else None
            continue
        if attr in ('enable_proxy_platform', 'extra_enable_proxy'):
            val = str(raw).replace('，', ',').split(',') if raw else None
            setattr(cfg, 'enable_proxy_platform_list' if attr == 'enable_proxy_platform'
                    else 'extra_enable_proxy_platform_list', val)
            continue
        setattr(cfg, attr, _cast(raw, type_hint, default))

    # ---- 推送配置 ----
    for ini_key, (attr, default, type_hint) in PUSH_INI_KEYS.items():
        raw = _read_value(parser, '推送配置', ini_key, default, encoding, config_file)
        setattr(cfg, attr, _cast(raw, type_hint, default))

    # ---- Cookie ----
    for key, ini_key in COOKIE_INI_KEYS.items():
        cfg.cookies[key] = _read_value(parser, 'Cookie', ini_key, '', encoding, config_file)

    # ---- 账号密码 / Authorization ----
    for key, (section, ini_key, default) in ACCOUNT_INI_KEYS.items():
        raw = _read_value(parser, section, ini_key, default, encoding, config_file)
        if section == 'Authorization':
            cfg.tokens[key] = raw
        else:
            cfg.accounts[key] = raw

    return cfg


def update_config_value(config_file: str, section: str, key: str, new_value: str,
                        encoding: str = 'utf-8-sig') -> None:
    """更新 config.ini 中某个键的值（供 new_cookies / new_token 回写等场景）。"""
    from src.utils import update_config
    update_config(config_file, section, key, new_value)
