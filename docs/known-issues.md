# 已知问题与遗留事项

本文记录**当前版本仍未修复**或**本次未纳入范围**的问题，供后续排期。已修复的历史问题
（例如 WebUI 删除任务后仍继续录制）见 `README.md` 的部署章节与相关提交记录。

排查问题时请先执行 `./deploy/install.sh --check`：它能区分"部署出错"与"代码 bug"，
避免再出现"两部分代码来自不同版本、只会在某条操作路径上炸"的情况。

---

## 1. 直接编辑 `URL_config.ini` 删除任务不会停止录制（严重）

**症状**：手工（或其它工具）从 `config/URL_config.ini` 里删掉某一行后，该任务从 WebUI
列表消失，但对应线程与 ffmpeg **继续录制**，直到进程重启。这正是 WebUI 删除路径已修复
的那个症状，只不过触发入口换成了文件编辑。

**原因**：停止录制依赖 `state` 里的 URL 停止标记，而该标记**只有 WebUI 删除接口会登记**：

```
grep -rn "request_stop" --include='*.py' .
./src/state.py:112:def request_stop(url: str) -> None:        # 定义
./webui/app.py:283:            state.request_stop(u)        # 唯一调用点
```

录制线程只在四个位置检查 `stop_requested()`（`main.py:468`、`main.py:511`、`main.py:660`、
`main.py:724`），因此没有标记就永远不会退出。

同时主循环对运行列表**只增不减**，不会反向核对"配置文件里还有没有这个任务"：

- `main.py:1571`–`main.py:1583`：遍历当前配置中的 URL，`url_tuple[1] not in running_list`
  时才启动线程并 `running_list.append(...)`；
- `main.py:427`–`main.py:428`（`clear_record_info`，暂停时移除）、`main.py:438`–`main.py:439`
  （`handle_removed_task`，WebUI 删除/停止请求命中时移除）是仅有的两处移除。

也就是说：配置文件被外部删掉的行，没有任何代码路径会去 `request_stop`。

**影响**：磁盘被无声写满、多路 ffmpeg 抢占带宽；用户以为任务已删除。

**建议修法**：在主循环里加一步反向核对（"配置里没有的 URL 就请求停止"）：

```python
known = {t[1] for t in text_no_repeat_url} | set(url_comments) | set(not_record_list)
for url in list(running_list):
    if url not in known:
        state.request_stop(url)      # 复用现有的停止标记机制
```

**注意**：不能简单写 `if url not in 配置里的 URL`，否则会误停"自动更新直播间地址"的线程——
该流程（`main.py:728`–`main.py:734`）把配置文件里的旧地址改写成新地址，而运行中的线程仍持有
旧地址并留在 `running_list` 里；`not_record_list` 正是为此存在，必须一并视为"已知"。
改完请补一条回归测试：删掉配置行后线程应在一个循环周期内退出。

---

## 2. WebUI 无鉴权；Docker 方式会把管理接口发布到网络（严重·安全）

**现状**：`webui/app.py` 没有任何认证/授权逻辑（无 `Depends`、无 Basic/Bearer 校验），
所有接口对能访问该端口的人完全开放，包括：

| 接口 | 风险 |
|---|---|
| `PUT /api/config` | 改写 `config.ini`，其中含 Cookie / Token / 推送密钥 |
| `DELETE /api/tasks`、`PUT /api/tasks/comment` | 增删任务、停止录制 |
| `POST /api/tasks` | 任意添加任务（可被用于磁盘写满） |
| `GET /api/videos` + `/videos/...` | 列出并下载全部录制文件 |
| `GET /api/logs` | 泄露直播源地址、平台 Cookie 相关报错 |

**暴露面**：`main.py:93` 的 `WEBUI_HOST` 默认值是 `0.0.0.0`：

```python
webui_host = os.environ.get('WEBUI_HOST', '0.0.0.0')
```

- `deploy/install.sh` 生成的 systemd 单元固定 `WEBUI_HOST=127.0.0.1`，**仅本机监听**，
  由反向代理（Caddy/Nginx）+ Basic Auth 保护 —— 这是推荐部署方式；
- 但 `docker-compose.yaml:12`–`14` 以 `python main.py --web` 启动、既未设置 `WEBUI_HOST`
  又把端口发布为 `"8000:8000"`（绑定所有网卡），相当于**把上述管理接口直接暴露到网络**；
- 手工执行 `python main.py --web` 也会监听 `0.0.0.0`。

**影响**：未授权即可读写配置、删除任务、下载录像。

**建议修法**（按优先级）：

1. Docker 部署改为 `127.0.0.1:8000:8000` 或显式 `WEBUI_HOST=127.0.0.1`，
   与 systemd 保持一致；
2. 在 README / compose 注释中写明"内置 WebUI 无鉴权，必须由反向代理提供认证，
   不要把端口直接暴露到公网"；
3. 若要彻底解决：为 `create_app` 加可选鉴权（`WEBUI_TOKEN` 环境变量 + 依赖校验），
   或在 `--web` 路径下检测到监听非回环地址且未配置令牌时拒绝启动。

---

## 3. 运行状态仅存于内存，重启后丢失（轻微）

**现象**：进程重启后，`/api/tasks` 中任务的 `anchor`、`recording_seconds`、`file` 归零；
**被暂停（注释）**的任务因不创建线程、不会重新注册，`status` 会一直返回 `unknown`
（例：`https://live.douyin.com/24090099997`）。

**原因**：`src/state.py` 的任务表是进程内字典（`_tasks`），无持久化；被注释的任务在
`main.py` 中不进入线程创建流程，因此不会有任何一次 `register_task`。

**说明**：这是**显示层之外**的问题，不是 UI bug —— 前端不直接用该字段，而是依据
配置里的 `commented` 渲染：

```javascript
// webui/static/index.html:930
const label = t.commented ? '已暂停' : (STATUS_LABEL[rawStatus] || '未知');
```

因此自带的 WebUI 会正确显示"已暂停"。仅当第三方直接消费 `/api/tasks` 时才会看到
`unknown`。

**建议修法**（如需）：应用启动时用配置文件初始化一次状态表（被注释的任务直接标为
`stopped`），或把任务状态落盘。属于体验优化，不影响录制正确性。

---

## 4. `main` 与 `desktop-tauri` 分支分叉（维护性）

`desktop-tauri` 是独立的一支（Tauri 桌面端外壳 + Vue3 前端 + Python sidecar），与 `main`
差异约 90 个文件、+9462/−2030 行，包含 `main` 没有的能力：

- `DLR_DATA_DIR`：把 `config/`、`downloads/` 重定向到可写的数据目录（安装包资源区只读），
  `main.py` 与 `webui/server.py` 均据此解析路径；
- `DLR_NO_TUI`：跳过 TUI 刷屏线程（避免清屏转义码污染 stdout）；
- WebUI 端口 `0` 时自动分配空闲端口，并打印 `DLR_WEBUI_READY:` 供壳解析。

**注意**：两条分支的 `src/url_config.py` 曾分别处于"新"与"旧"状态，正是本文开头那类
混装事故的来源。合并时请**整分支重装**（`sudo ./deploy/install.sh --upgrade`，必要时加
`--allow-dirty`），不要手工挑选文件覆盖安装目录；`--check` 会在混装时直接报 `skew`。

`main` 的部署校验不感知 `DLR_DATA_DIR`（清单只覆盖代码文件，运行数据本就不参与），
但 `deploy/install.sh` 尚未处理桌面端的数据目录约定，合并时需一并确认。

---

## 5. 部署校验的定位（不是防篡改）

`src/deploy_check.py` 是**运维一致性检查**，不是安全控制：清单是明文 JSON、与代码同处
一个目录，且 `deploy/install.sh` 会把整个目录 `chown` 给服务用户，因此**能写这个目录的人
可以同时改代码和清单让校验通过**。

它能发现：复制不完整、残留旧文件、手工把不同版本的文件混在一起、事后有人改了安装目录
里的文件。
它不能替代：权限控制、只读挂载、签名校验。

另外，用 `--allow-dirty` 安装的"自洽混合树"（清单按当时工作区生成）无法被自动发现——
校验只会持续提示"该部署含本地改动"。要更强的保证，需要把清单放到服务用户不可写的位置
（例如 `/var/lib` 下 root 所有）并对代码目录只读。

**运维文件放置约定**（避免升级时丢失）：

- 安装目录**顶层**的文件（如 `.env`、`start.sh`）与 `config/ downloads/ logs/ backup_config/`
  等运行数据：不参与校验，升级时保留；
- **项目目录内部**的文件（如 `webui/caddy.conf`）：升级会随项目目录整体替换而删除
  （无法与"上一版残留的代码文件"可靠区分）。安装脚本会在删除前列出这些文件提醒搬走。
