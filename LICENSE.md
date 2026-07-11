# Repository Licensing / 仓库许可

This repository contains materials governed by different terms. No single
license applies to every file in the repository.

本仓库包含适用不同条款的内容，不存在一份覆盖所有文件的单一许可证。

## 1. Bridge software / 桥接软件

Unless a file or directory is expressly excluded below, the software and
documentation developed for this repository are licensed under the MIT
License. This includes the bridge runtime, control panel, deployment and helper
scripts, and project documentation.

除下文明确排除的文件或目录外，本仓库开发和维护的软件与文档采用 MIT
License，包括 Bridge 运行时、控制面板、部署及辅助脚本和项目文档。

Copyright (c) 2026 L1lyW33p5

The complete MIT text is available in [`LICENSES/MIT.txt`](LICENSES/MIT.txt).

MIT 许可证全文见 [`LICENSES/MIT.txt`](LICENSES/MIT.txt)。

Runtime dependencies are not relicensed by this MIT grant. In particular, the
modern input UI uses separately installed PySide6 under the applicable Qt for
Python LGPLv3, GPLv3, or commercial terms; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

运行时依赖不因上述 MIT 授权而被重新许可。现代输入框使用另行安装的 PySide6，
其继续适用 Qt for Python 的 LGPLv3、GPLv3 或商业许可；详见
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

## 2. Taromati2-derived materials / Taromati2 衍生内容

The following materials are **not** licensed under the MIT License:

- `patches/taromati2/**`
- `patches/hermes/kikka/SOUL.md`
- `banner.png`

These materials are excluded from the MIT License. They contain or derive from
Taromati2/Kikka materials and are made available only to the extent permitted
by the applicable rights holders and the current Taromati2 terms. The upstream
terms permit non-profit handling and prohibit commercial use of the upstream
project's modified portions; third-party portions remain subject to their
respective permissions.

这些文件包含或衍生自 Taromati2/Kikka 内容，仅可在相关权利人许可及
Taromati2 最新条款允许的范围内使用。上游条款允许非盈利处置，并禁止将上游
项目修改部分用于商业用途；其中属于其他作者的部分仍适用各自的许可。

See [`patches/taromati2/LICENSE.md`](patches/taromati2/LICENSE.md) and the
[upstream Taromati2 terms](https://github.com/Taromati2/Taromati2#关于许可证).

详见 [`patches/taromati2/LICENSE.md`](patches/taromati2/LICENSE.md) 与
[Taromati2 上游条款](https://github.com/Taromati2/Taromati2#关于许可证)。

Commercial use of these excluded materials requires separate permission from
all applicable rights holders.

上述排除内容如需用于商业用途，必须另行取得所有相关权利人的许可。

## 3. Third-party rights / 第三方权利

All third-party names, characters, artwork, software, and trademarks remain the
property of their respective rights holders. Nothing in this repository grants
rights beyond those expressly provided by the applicable license or rights
holder. If this notice conflicts with a third-party license, the third-party
license controls for that material.

所有第三方名称、角色、图像、软件及商标的权利归各自权利人所有。本仓库不会
授予超出相关许可证或权利人明确许可范围的权利。若本说明与第三方许可冲突，
该第三方内容以其原许可为准。

See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and
[`DISCLAIMER.md`](DISCLAIMER.md) for attribution and project-specific risk
notices.

第三方归属与项目专项风险说明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)
和 [`DISCLAIMER.md`](DISCLAIMER.md)。
