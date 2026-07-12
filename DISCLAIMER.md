# Project Disclaimer / 项目免责声明

## 中文

本项目按“原样”提供，不对适销性、特定用途适用性、无错误运行、数据完整性或不侵权作出任何明示或默示保证。在适用法律允许的最大范围内，项目作者不对因使用或无法使用本项目而产生的直接、间接、附带、特殊或后果性损失承担责任。

使用本项目还应特别注意：

- 部署与控制脚本会复制或覆盖本地 SSP、Taromati2 和 Hermes profile 文件，并可创建或删除登录自启动任务。脚本提供的备份与哈希校验不能替代用户自己的完整备份。
- Bridge 会调用本地或第三方 AI、TTS 和模型服务。生成内容、服务可用性、费用、隐私政策及数据处理方式由对应服务和用户配置决定。
- 启用“屏幕回应”后，Bridge 会周期性截取主显示器的一帧，并经由本地 Hermes Gateway 发送给用户配置的模型服务。画面可能包含私人或敏感信息，用户应按需在控制面板中关闭该功能。
- 部署脚本会访问 `api.country.is` 或 `ipapi.co` 判断公网 IP 所在地区，以选择 pip 软件源；这些第三方服务会收到用户请求的公网 IP。
- Web 控制面板仅设计为在 `127.0.0.1` 上由受信任的本机用户使用，不应通过端口转发、反向代理或修改监听地址暴露到其他网络。
- 用户应自行保护 API key、`.env`、Hermes profile、记忆文件及其他私人数据，并在执行前检查脚本、补丁及目标路径。
- Taromati2/Kikka 相关内容可能包含成熟、冒犯性或令人不适的主题；其上游免责声明与使用条款继续适用。
- 本项目不是 SSP、Taromati2、YAYA、Nous Research、Hermes Agent 或任何模型服务商的官方项目，也不代表这些项目或权利人对本项目提供背书。

本说明不会扩大任何第三方许可，也不会排除适用法律中不可排除的责任。

## English

This project is provided "as is", without express or implied warranties of
merchantability, fitness for a particular purpose, error-free operation, data
integrity, or non-infringement. To the maximum extent permitted by applicable
law, the project authors are not liable for direct, indirect, incidental,
special, or consequential losses arising from use of, or inability to use, the
project.

Users should also understand that:

- Deployment and control scripts copy or replace local SSP, Taromati2, and
  Hermes profile files and may create or remove logon-startup tasks. Scripted
  backups and hash checks are not a substitute for the user's own complete
  backup.
- The Bridge can call local or third-party AI, TTS, and model services. Output,
  availability, cost, privacy policies, and data handling depend on those
  services and the user's configuration.
- When screen reactions are enabled, the Bridge periodically captures one
  frame from the primary display and sends it through the local Hermes Gateway
  to the model service configured by the user. Captures may contain private or
  sensitive information; disable this watcher in the control panel when needed.
- The deployment script contacts `api.country.is` or `ipapi.co` to determine
  the public-IP region and select a pip index. Those services receive the
  request's public IP address.
- The web control panel is designed only for a trusted local user on
  `127.0.0.1`. Do not expose it through port forwarding, a reverse proxy, or a
  changed bind address.
- Users are responsible for protecting API keys, `.env`, Hermes profiles,
  memory files, and other private data, and for reviewing scripts, patches, and
  target paths before execution.
- Taromati2/Kikka-related material may contain mature, offensive, or disturbing
  themes. The upstream disclaimer and terms remain applicable.
- This is not an official project of SSP, Taromati2, YAYA, Nous Research,
  Hermes Agent, or any model provider, and no endorsement is implied.

This notice does not expand any third-party license or exclude liability that
cannot lawfully be excluded.
