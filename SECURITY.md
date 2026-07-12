# Security / 安全说明

## 中文

本项目仅面向受信任的本机 Windows 用户。Web 控制面板固定监听 `127.0.0.1`，并拒绝非本机 `Host`/`Origin` 请求；请勿通过端口转发、反向代理或修改监听地址将其暴露到局域网或互联网。

如果发现安全问题，请优先使用 GitHub 仓库的 **Private vulnerability reporting** 功能联系维护者。请勿在公开 Issue 中粘贴 API key、`.env`、Hermes profile、`SOUL.md`、`MEMORY.md`、`USER.md`、日志或截图。

报告中请包含受影响版本、复现步骤、预期影响以及已验证的缓解方法。不要测试或访问不属于你的机器、账号或数据。

本项目当前以 Hermes Agent `0.18.x` 的原生 Windows 安装作为验证基线。

## English

This project is intended for a trusted local Windows user only. The web control
panel binds to `127.0.0.1` and rejects non-local `Host`/`Origin` requests. Do
not expose it to a LAN or the Internet through port forwarding, a reverse
proxy, or a changed bind address.

Please report vulnerabilities through the repository's **Private vulnerability
reporting** feature when available. Do not paste API keys, `.env` files,
Hermes profiles, `SOUL.md`, `MEMORY.md`, `USER.md`, logs, or screenshots into a
public issue.

Include the affected version, reproduction steps, expected impact, and any
tested mitigation. Do not test machines, accounts, or data you do not own.

The current compatibility baseline is a native Windows installation of Hermes
Agent `0.18.x`.
