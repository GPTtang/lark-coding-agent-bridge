# tmux-agent-bridge（实验性，Python）

一个独立的轻量桥接程序：每个飞书 / Lark 群对应一个本机项目目录和一个 agent（Claude Code 或 Codex），**群里的消息会直接打进终端里正在运行的 agent**（[Muxy](https://github.com/muxy-app/muxy) 窗格或 tmux 窗格），屏幕上实时可见；agent 每轮结束后，结果自动推回群里。

它和主项目 `lark-channel-bridge` 互相独立，不共用代码和配置。主项目以无界面方式运行 agent，用流式卡片回复；这个程序适合「人主要在终端里干活，群只是远程入口」的用法。

## 功能

- **终端注入**：按以下顺序找发送目标：
  1. **Muxy 窗格**：通过 Muxy 自带的 CLI（`list-panes` / `read-screen` / `send` / `send-keys`）查找。Muxy 的协议一行就是一条命令，所以多行消息会被合并成一行。
  2. **tmux 窗格**：用括号粘贴（bracketed paste）加回车发送，多行消息也只会提交一次。
  3. 都找不到时，回退为无界面运行（`claude -p --resume` / `codex exec resume`）。

  窗格要满足两个条件才会被选中：工作目录是该群对应的目录，屏幕底部能看到对应 agent 的界面。`FEISHU_INJECT_MODE` 可以设为 `auto`（默认）、`muxy`、`tmux` 或 `off`。
- **弹窗保护**：发送前先读屏幕底部，看到权限确认、hook 审核、更新提示这类弹窗时不发送，并在群里提示。否则粘贴进去的文字会被当成按键，比如一个 `t` 就可能误点「信任 hook」。
- **自动拉起 + 自动恢复（可选，tmux）**：supervisor 每 30 秒运行一次，保证每个群都有一个 `lark-<群名>` tmux 会话在运行 agent。agent 退出后，会续接这个 tmux 会话自己的 session 重新拉起。
- **结果回推**：通过 Claude Code / Codex 的 hook 实现：Stop 时推送本轮结果，Notification 时推送提醒。
- **图片**：图片、富文本里的图片都会下载到本地交给 agent，Codex 用 `--image`。只发图片时先暂存起来，等下一条文字到了再一起处理。
- **Codex 会话被占用**：Codex 续接时遇到 `already has an active writer`，会自动改用新会话处理。
- **群内命令**（只执行群主以及 `FEISHU_ALLOWED_OPEN_IDS` 里的人发的消息）：

  | 命令 | 作用 |
  |---|---|
  | `/status` | 显示目录、agent、session，以及消息会发到哪里 |
  | `/new <任务>` | 在后台新开一个会话来执行 |
  | `//<命令>` | 把 `/<命令>` 原样发给终端里的 CLI（如 `//status`、`//model`、`//compact`），2 秒后把终端画面发回群里 |
  | `/screen` | 把终端当前画面发回群里 |
  | `/esc` | 在终端里按一次 Esc，关掉面板或弹窗 |

  CLI 斜杠命令不算一轮对话，不会触发 Stop hook，所以执行结果用截屏发回群里。像 `//status` 这样会打开面板的命令执行完后，面板会被识别为弹窗，后面的消息会暂停发送，发 `/esc` 关掉面板即可。这些命令都需要 Muxy 或 tmux 窗格。

## 安装（macOS）

前置：Python 3.10+、已登录的 `claude` 和 / 或 `codex`；终端注入需要 [Muxy](https://github.com/muxy-app/muxy) 或 tmux（只用后台运行时两者都不需要）。

```bash
cd contrib/tmux-agent-bridge
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

1. **飞书 / Lark 应用**：在开发者后台创建企业自建应用，添加「机器人」能力，然后批量开通权限：
   ```json
   {"scopes":{"tenant":["im:chat:readonly","im:message","im:message:send_as_bot","im:message.group_msg"],"user":[]}}
   ```
   发布版本，把机器人拉进要用的群（网页版在「添加群成员」里搜机器人名）。
2. **凭证**：新建 `.env`（执行 `chmod 600 .env`）：
   ```
   FEISHU_APP_ID=cli_xxx
   FEISHU_APP_SECRET=xxx
   # 国内飞书：FEISHU_DOMAIN=https://open.feishu.cn（默认是 Lark 国际版）
   ```
3. **群映射**：把 `groups.example.json` 复制为 `groups.json`，填写群名、目录和 agent，然后运行 `.venv/bin/python setup_map.py`。
4. **启动服务**：运行 `./ctl.sh install`，会注册两个 launchd 任务，桥接服务和 tmux supervisor，开机自动启动。
5. **事件订阅**：服务启动后，回到开发者后台的「事件与回调」，选择「使用长连接接收事件」，添加 `im.message.receive_v1`，然后重新发布一次版本。
6. **Hooks**（把 `<DIR>` 换成本目录的绝对路径）：
   - Claude Code `~/.claude/settings.json`：
     ```json
     {"hooks": {
       "SessionStart": [{"hooks": [{"type": "command", "command": "'<DIR>/.venv/bin/python' '<DIR>/notify.py' claude start", "timeout": 20}]}],
       "Stop":         [{"hooks": [{"type": "command", "command": "'<DIR>/.venv/bin/python' '<DIR>/notify.py' claude stop", "timeout": 20}]}],
       "Notification": [{"hooks": [{"type": "command", "command": "'<DIR>/.venv/bin/python' '<DIR>/notify.py' claude notification", "timeout": 20}]}]
     }}
     ```
   - Codex `~/.codex/hooks.json`：
     ```json
     {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "'<DIR>/.venv/bin/python' '<DIR>/notify.py' codex stop", "timeout": 20}]}]}}
     ```
     Codex 第一次运行时会要求审核这个 hook，需要手动选信任（只需一次）。

## 日常使用

```bash
.venv/bin/python tmux_open.py <群名>             # 进入该群的 agent 窗口（Ctrl-b d 退出，agent 继续运行）
.venv/bin/python tmux_supervisor.py status      # 查看各会话状态
.venv/bin/python tmux_supervisor.py stop|start  # 暂停 / 恢复自动拉起（stop 会关掉 lark-* 会话）
./ctl.sh status | logs | restart                # 桥接服务
```

- 关闭注入、全部改为无界面运行：在 `.env` 里写 `FEISHU_INJECT_MODE=off`，然后重启服务。
- 只用 Muxy、不需要 tmux 自动拉起：运行 `tmux_supervisor.py stop` 即可（暂停后不会再新建 tmux 会话）。
- tmux 里的 Codex 启动时带 `-c check_for_update_on_startup=false`，不会卡在更新提示上；全局配置不变。
- 你在终端里正打着字时，群消息会插进来，和没打完的内容拼在一起。
- 运行时文件（`.env`、`groups.json`、`dir_map.json`、`sessions.json`、`tmux_sessions.json`、`inbox/`、日志）都不进 git。

## 测试

```bash
.venv/bin/python -m pytest -q
```
