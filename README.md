<p align="right">
  简体中文 <strong>・</strong>
  <a href="README_EN.md">English</a>
</p>

![](banner.png)

此项目是一个面向 `Windows` 平台的桥接工具，专为 SSP (ukagaka/伺か) 的 ghost [Taromati2](https://github.com/Taromati2/Taromati2) 与本地 Hermes Agent 之间的通信而设计，并附带一个简易的 web 前端面板。

籍此，通过用户与人格的交谈或交互，Kikka(橘花) 得益于 Hermes Agent 的运行框架，能对用户的输入进行回应、侧写用户画像并不断完善自身记忆，也可以使用其拥有的技能和工具完成用户的指令。

为了使桥接及相关机制能正常运行，另外做了 ghost 侧的桥接适配修改，因而，启用桥接前需要安装[专用的集成补丁](#补丁与备份)。

## 功能特性

- 双击 Ctrl 唤起输入框，把文本发送给 Kikka 并得到回应。
- 支持 TTS 生成与播放，使 ghost 的回复可以被听见；默认使用 Edge TTS 神经语音 `zh-CN-XiaoyiNeural`，并通过 Windows 内置 MCI 播放，无需额外安装 ffplay。
- 提供 Taromati2 / YAYA 集成补丁，用于接入 kikka 养成变量、持久气球和静默控制等能力。
- 根据“橘花养成系统”的 7 个变量，Kikka 将在桥接启动后的特定期间内触发屏幕回应或主动谈话。屏幕回应会截取主显示器的一帧，并通过本地 Hermes Gateway 发送给用户配置的模型服务。

## 快速开始（自动部署）

### 前置条件

- Windows 10/11 上已经安装原生版 Hermes Agent `0.18.x`；安装后请重新打开终端，使 `hermes.exe` 能被 PATH 检测到。脚本也会检查 Hermes 的默认安装位置及名为 `hermes` 的环境变量。
- 待部署的 SSP 目录有效且人格 Taromati2 存在。
- 部署期间可以访问 GitHub、PyPI/清华 TUNA、IP 地区检测服务以及用户配置的模型和 Edge TTS 服务。

### 开始部署

直接将本仓库下载并解压至你的 SSP 目录（`ssp.exe` 所在位置），或者：

进入 SSP 目录后，Shift + 右键空白处，点击“在终端打开”。

在弹出的 `CMD` 终端窗口内键入以下命令并回车：

```bat
curl.exe -fL "https://github.com/L1lyW33p5/hermes-ssp-bridge-incarnadine/archive/refs/heads/main.zip" -o "%TEMP%\hermes-ssp-bridge-incarnadine.zip" && tar.exe -xf "%TEMP%\hermes-ssp-bridge-incarnadine.zip" -C . --strip-components=1 && del "%TEMP%\hermes-ssp-bridge-incarnadine.zip"
```

如果是 `PowerShell` 终端：

```powershell
cmd /c 'curl.exe -fL "https://github.com/L1lyW33p5/hermes-ssp-bridge-incarnadine/archive/refs/heads/main.zip" -o "%TEMP%\hermes-ssp-bridge-incarnadine.zip" && tar.exe -xf "%TEMP%\hermes-ssp-bridge-incarnadine.zip" -C . --strip-components=1 && del "%TEMP%\hermes-ssp-bridge-incarnadine.zip"'
```

然后等待命令执行完毕。

接着运行根目录中的 `自动部署脚本.bat`。环境检测通过后选择“开始部署”，脚本会：

1. 根据公网 IP 所在地区选择 pip 默认源或清华大学 TUNA PyPI 镜像，并从 `requirements.txt` 安装依赖。

2. 创建缺失的 Hermes `kikka` profile，并将 `patches/hermes/kikka/SOUL.md` 部署到 profile。

3. 为 kikka profile 配置仅监听 `127.0.0.1:8642` 的 Hermes API Server；缺少强密钥时会在本机生成随机密钥。随后安装 kikka gateway，并等待健康检查通过。

4. 安装集成补丁，将 `patches/taromati2/` 中的文件按相对路径覆盖到检测到的 `ghost/Taromati2`。

5. 对每个已复制文件执行 SHA-256 校验；校验全部通过后将显示部署完成菜单。

6. 自动生成或更新本机私有的 `.env` 路径配置，同时保留其中已有的其他配置。

部署完成后可在同一菜单中重新部署、管理 Bridge/gateway/web 控制面板的登录自启动、进入 Hermes 官方模型向导配置 Provider 和模型，或恢复脚本创建的 patch/runtime/SOUL 备份。

## Web 控制面板

运行根目录中的 `控制面板开关.bat` 以快速启动或关闭面板。

服务启动后访问：

```text
http://127.0.0.1:1313
```

控制面板可查看 Bridge 与 gateway 状态、启动或停止进程、查看日志，并编辑当前 gateway profile 的相关文件。服务只监听 `127.0.0.1`，会拒绝非本机 Host/Origin 请求；请勿通过端口转发或反向代理将其公开。

## 补丁与备份

- `patches/hermes/kikka/SOUL.md` 是部署到 `kikka` profile 的人格模板。覆盖不同内容的现有 `SOUL.md` 前，脚本会在 profile 目录中创建带时间戳的备份。
- `patches/taromati2/` 保存 bridge 所需的 YAYA / ghost 侧集成修改，包括 SSTP handler、养成变量、持久气球与静默控制等功能。
- 覆盖不同内容的 Taromati2 文件前，脚本会将原文件备份到本机的 `patch-backups/<时间戳>/taromati2/`。
- Bridge 首次调整 SSP/Taromati2 的本地 TTS 配置前，会备份到 `patch-backups/<时间戳>/runtime/`；不会修改其他 ghost 的 YAYA 配置。
- 每次启动脚本时，只要 `kikka` profile 及其中的 `SOUL.md` 已存在，且 Taromati2 目标文件与补丁哈希一致，就会直接显示部署完成菜单。脚本不会校验 `SOUL.md` 的内容哈希，因此用户部署后的自定义修改会被保留并视为有效。
- 部署菜单的“备份恢复”功能可恢复 patch/runtime 备份或最新的 `SOUL.md` 部署备份；恢复前还会保存当时的目标文件。
- [更详细的补丁说明见此](patches/README.md)

## 仓库结构

| 路径 | 用途 |
| --- | --- |
| `自动部署脚本.bat` | 自动检测环境、安装依赖并部署 Hermes 与 Taromati2 补丁 |
| `控制面板开关.bat` | 启动/停止本地 web 控制面板并显示运行状态 |
| `scripts/auto_deploy.ps1` | 自动部署入口背后的 PowerShell 执行核心 |
| `hermes_bridge.py` | bridge 主运行时 |
| `bridge_wrapper.py` | bridge 启动包装器 |
| `hermes_input_modern.py` | 现代输入框 UI |
| `bridge_control/` | 本地网页控制面板 |
| `scripts/` | Windows 后台启动、任务计划注册和验证辅助脚本 |
| `patches/hermes/kikka/SOUL.md` | 自动部署到 Hermes `kikka` profile 的人格模板 |
| `patches/taromati2/` | Taromati2 / YAYA 集成补丁 |
| `requirements.txt` | Python 依赖列表 |
| `.github/workflows/validate.yml` | Windows 下的公开树、Python 与 PowerShell 自动验证 |
| `SECURITY.md` | 本地控制面板边界与漏洞报告说明 |
| `LICENSE.md` | 本仓库代码与第三方内容的混合许可范围 |
| `THIRD_PARTY_NOTICES.md` | 第三方项目、素材与依赖声明 |
| `DISCLAIMER.md` | 部署、AI 服务与第三方内容的风险说明 |

## 许可与免责声明

本仓库采用混合许可：本仓库提供的 Bridge、控制面板和部署脚本采用 MIT License；
`patches/taromati2/**`、Kikka `SOUL.md` 与 `banner.png` 不属于 MIT 授权范围，
继续适用 Taromati2 上游非商业条款及其他权利人的许可。

现代输入框使用另行安装的 PySide6；PySide6 继续适用 Qt for Python 的 LGPLv3、GPLv3 或商业许可，本仓库不再分发其二进制文件。

完整范围与条款见 [LICENSE.md](LICENSE.md)、[第三方声明](THIRD_PARTY_NOTICES.md)
和[项目免责声明](DISCLAIMER.md)。
