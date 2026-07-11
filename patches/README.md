# Hermes 与 Taromati2 集成补丁

<p>
  简体中文 |
  <a href="README_EN.md">English</a>
</p>

此目录保存为了接入 `hermes-ssp-bridge` 而修改的少量 Taromati2/YAYA `.dic` 文件。

这里不会包含完整的 SSP 安装、完整的 Taromati2 ghost、shell 资源、profile 运行状态、日志、缓存或个人 `aya_variable.cfg` 数据。

请在 Hermes 与 SSP/Taromati2 环境检测通过后，使用仓库根目录的 `自动部署脚本.bat` 应用这些文件。覆盖前会备份内容不同的目标文件，复制后会逐个执行 SHA-256 校验。

## 包含的补丁文件

- `ghost/master/dic/system/event_response.dic` — Bridge 所需的 SSTP handler，包括 `OnGetNurturance`、`OnSetNurturance` 和 `OnKikkaBalloon` 等。
- `ghost/master/dic/aya/master/shiori.dic` — 围绕 YAYA 事件路由实现的 Bridge 锁定与静默集成。
- `ghost/master/dic/system/anti_cheat.dic` — Bridge 所控制变量的持久化钩子。
- `ghost/master/dic/nurturance/nurturance.dic` — Bridge 控制养成变量时使用的数值保护。
- `ghost/master/dic/system/clock.dic` — 为 Bridge 运行时调整的本地事件行为。
- `ghost/master/dic/system/menu.dic` — Bridge 设置涉及的本地菜单与运行时集成。

## 许可

本目录不是统一采用 MIT License。Taromati2/YAYA 衍生文件适用上游非商业条款，
详见 [`taromati2/LICENSE.md`](taromati2/LICENSE.md) 和仓库根目录的
[`LICENSE.md`](../LICENSE.md)。
