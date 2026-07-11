# Third-Party Notices / 第三方声明

This project integrates with or contains modifications derived from third-party
projects. Inclusion in this list does not imply sponsorship, endorsement, or
affiliation.

本项目会与第三方项目集成，或包含基于第三方项目制作的修改。列出相关项目不
代表其对本项目提供赞助、背书或存在隶属关系。

## Taromati2 / Kikka

- Upstream project: <https://github.com/Taromati2/Taromati2>
- Upstream license section: <https://github.com/Taromati2/Taromati2#关于许可证>
- Upstream disclaimer: <https://github.com/Taromati2/Taromati2#免责声明>

`patches/taromati2/**` contains modified Taromati2/YAYA files. `banner.png` and
`patches/hermes/kikka/SOUL.md` also contain or describe Kikka/Taromati2-derived
material. These files are excluded from this repository's MIT grant and remain
subject to the upstream non-commercial terms and any rights held by other
contributors or character creators.

`patches/taromati2/**` 包含修改后的 Taromati2/YAYA 文件；`banner.png` 与
`patches/hermes/kikka/SOUL.md` 也包含或描述 Kikka/Taromati2 衍生内容。这些
文件不属于本仓库的 MIT 授权范围，仍受上游非商业条款以及其他贡献者或原角色
创作者所持权利约束。

## Hermes Agent

- Official project: <https://github.com/NousResearch/hermes-agent>
- License: MIT, copyright Nous Research
- License text: <https://github.com/NousResearch/hermes-agent/blob/main/LICENSE>

This repository communicates with a separately installed Hermes Agent. Hermes
Agent is not redistributed here. Its name is used only to describe
compatibility and integration.

本仓库与用户另行安装的 Hermes Agent 通信，并不再分发 Hermes Agent。相关名称
仅用于描述兼容性与集成关系。

## SSP, YAYA, Edge TTS, and Python dependencies

SSP, YAYA, Edge TTS, and packages listed in `requirements.txt` are third-party
software installed or obtained separately. Their respective licenses and terms
continue to apply. Their names are used only for compatibility and dependency
identification.

SSP、YAYA、Edge TTS 以及 `requirements.txt` 中的软件包均为另行安装或获取的
第三方软件，各自的许可证和条款继续有效。相关名称仅用于说明兼容性和依赖关系。

## Qt for Python / PySide6

- Official project: <https://doc.qt.io/qtforpython-6/>
- License options: LGPLv3, GPLv3, or the Qt commercial license

The modern input UI uses PySide6, which is installed separately from PyPI by
the deployment script and is not vendored in this repository. Users and
redistributors remain responsible for complying with the Qt for Python license
terms applicable to their distribution method.

现代输入框使用 PySide6。部署脚本会从 PyPI 另行安装该依赖，本仓库不直接再分发
其二进制文件。用户及再分发者仍需根据自己的分发方式遵守 Qt for Python 的
LGPLv3、GPLv3 或商业许可条款。
