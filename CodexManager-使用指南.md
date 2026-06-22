# CodexManager 使用指南

## 两种模式

| 模式 | 插件模式（默认） | Manager 模式 |
|------|:-----------:|:----------:|
| 工具 | VS Code 插件 | Codex CLI 终端 |
| 路由 | 直连 OpenAI | localhost:48761 → 账号池 |
| 用途 | 日常开发 | 多账号轮询、省额度 |

**两种模式会话互通**，存在本地 `~/.codex/state_5.sqlite`，不会丢。

## 切换命令

```bash
# 切到 Manager 模式（没额度时用）
bash ~/.codex/switch-to-manager.sh
codex    # 终端里用

# 切回插件模式（日常开发用）
bash ~/.codex/switch-to-plugin.sh
# 然后在 VS Code 里 Ctrl+Shift+P → Reload Window
```

## 服务管理

```bash
cd /home/huangzhenhua/workspace/token_is_all_you_need

# 查看状态
docker ps --filter name=codexmanager

# 重启
docker compose restart

# 查看日志
docker logs -f codexmanager
```

## Web 管理界面

- 账号管理: http://127.0.0.1:48761/accounts/
- 平台密钥: http://127.0.0.1:48761/apikeys/
- 请求日志: http://127.0.0.1:48761/logs/

## 注意事项

- 切换模式前建议先结束当前对话
- Manager 模式依赖代理（Clash Verge 需开美国节点）
- 会话数据定时备份: `cp -r ~/.codex/sessions ~/.codex/*.sqlite ~/backup/`
