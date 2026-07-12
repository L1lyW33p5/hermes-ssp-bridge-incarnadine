# Hermes 与 Taromati2 集成补丁

<p>
  简体中文 |
  <a href="README_EN.md">English</a>
</p>

此目录保存为了接入 `hermes-ssp-bridge` 而修改的少量 Taromati2/YAYA `.dic` 文件。

这里不会包含完整的 SSP 安装、完整的 Taromati2 ghost、shell 资源、profile 运行状态、日志、缓存或个人 `aya_variable.cfg` 数据。

请在 Hermes 与 SSP/Taromati2 环境检测通过后，使用仓库根目录的 `自动部署脚本.bat` 应用这些文件。若 SSP 正在运行，脚本会要求用户先正常退出，避免 YAYA 在逐文件覆盖期间通过 `last_work_able_dic` 恢复旧辞书；部署结束后会尝试在普通用户会话中重新启动 SSP。覆盖前会备份内容不同的目标文件，复制后会逐个执行 SHA-256 校验。

## 包含的补丁文件

- `ghost/master/dic/system/event_response.dic` — Bridge 所需的 SSTP handler，包括 `OnGetNurturance`、`OnSetNurturance` 和 `OnKikkaBalloon` 等。
- `ghost/master/dic/aya/master/shiori.dic` — 围绕 YAYA 事件路由实现的 Bridge 锁定与静默集成。
- `ghost/master/dic/system/anti_cheat.dic` — Bridge 所控制变量的持久化钩子。
- `ghost/master/dic/nurturance/nurturance.dic` — Bridge 控制养成变量时使用的数值保护。
- `ghost/master/dic/system/clock.dic` — 为 Bridge 运行时调整的本地事件行为。
- `ghost/master/dic/system/menu.dic` — Bridge 设置涉及的本地菜单与运行时集成。
- `ghost/master/dic/communicate/talk/pseudoAI.dic` — 将 ghost 台词以 UTF-8 写入 Bridge TTS 队列，并阻止重复启动原生 `voice.vbs`。
- `ghost/master/dic/system/FileDrop.dic` — 将拖入 ghost 的图片交给 Bridge 多模态处理。
- `ghost/master/dic/other/Miniuse.dic` — 在补零工具中先将参数转为字符串，避免数值参数触发类型错误。
- `ghost/master/dic/other/kikkastock.dic` — 补齐文件读取后的关闭操作，避免持续运行时泄漏文件句柄。

## 许可

本目录不是统一采用 MIT License。Taromati2/YAYA 衍生文件适用上游非商业条款，详见 [`taromati2/LICENSE.md`](taromati2/LICENSE.md) 和仓库根目录的 [`LICENSE.md`](../LICENSE.md)。
