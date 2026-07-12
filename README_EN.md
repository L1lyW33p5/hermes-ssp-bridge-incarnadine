<p align="right">
  <a href="README.md">简体中文</a> <strong>・</strong>
  English
</p>

![](banner.png)

This project is a bridge. Built for `Windows`, it connects the SSP (ukagaka/伺か) ghost [Taromati2](https://github.com/Taromati2/Taromati2) with a local Hermes Agent and includes a simple web frontend panel.

With this bridge, Kikka can use the Hermes Agent runtime to respond to user input through conversation and interaction, build a user profile, continuously improve her own memory, and use her available skills and tools to complete user requests.

To ensure the Bridge and its related mechanisms can operate correctly, this project includes ghost-side integration changes. Install the [dedicated integration patches](#patches-and-backups) before enabling the Bridge.

## Key features

- Open the input box with a double Ctrl press, send text to Kikka, and receive a reply.
- Generate and play TTS so the ghost's replies can be heard. The default voice is the Edge TTS neural voice `zh-CN-XiaoyiNeural`.
- Provide Taromati2 / YAYA integration patches for kikka nurturance variables, persistent balloons, silence control, and related bridge features.
- Based on the seven variables from the “Kikka nurturance system”, Kikka can trigger screen-reaction or active-talk events during specific periods after the bridge starts.

## Quick start (automatic deployment)

### Prerequisites

- Native Hermes Agent `0.18.x` is installed on Windows 10/11. Open a new terminal after installation so PATH can find `hermes.exe`; the script also checks the default Hermes install path and an environment variable named `hermes`.
- The target SSP directory is valid and contains the Taromati2 ghost.

### Start deployment

Once the environment is ready,

Download this repository and extract it directly into your SSP directory (the directory containing `ssp.exe`), or:

Open the SSP directory, then Shift + right-click an empty area and select **Open in Terminal**.

In the `CMD` terminal that opens, enter the following command and press Enter:

```bat
curl.exe -fL "https://github.com/L1lyW33p5/hermes-ssp-bridge-incarnadine/archive/refs/heads/main.zip" -o "%TEMP%\hermes-ssp-bridge-incarnadine.zip" && tar.exe -xf "%TEMP%\hermes-ssp-bridge-incarnadine.zip" -C . --strip-components=1 && del "%TEMP%\hermes-ssp-bridge-incarnadine.zip"
```

If the terminal is `PowerShell`, run:

```powershell
cmd /c 'curl.exe -fL "https://github.com/L1lyW33p5/hermes-ssp-bridge-incarnadine/archive/refs/heads/main.zip" -o "%TEMP%\hermes-ssp-bridge-incarnadine.zip" && tar.exe -xf "%TEMP%\hermes-ssp-bridge-incarnadine.zip" -C . --strip-components=1 && del "%TEMP%\hermes-ssp-bridge-incarnadine.zip"'
```

Wait for the command to finish.

Then run `自动部署脚本.bat` from the root. After the environment checks pass, select **Start Deployment**. The script:

1. Selects the default pip index or the Tsinghua TUNA PyPI mirror based on the public IP location, then installs `requirements.txt`.

2. Creates the Hermes `kikka` profile when missing and deploys `patches/hermes/kikka/SOUL.md`.

3. Configures the kikka profile's Hermes API Server on `127.0.0.1:8642`, generating a strong local key when needed. It then installs the kikka gateway and waits for its health check.

4. Installs the integration patches by copying files below `patches/taromati2/` to the detected `ghost/Taromati2` using their relative paths.

5. SHA-256-verifies every copied file. The completed menu is shown after all checks pass.

6. Creates or updates the private local `.env` path settings while preserving unrelated existing settings.

After deployment, the same menu can redeploy, manage logon startup for the Bridge/gateway/web panel, open the official Hermes model wizard (which creates `config.yaml` when it is missing), or restore patch/runtime/SOUL backups created by the scripts.

## Web control panel

Run `控制面板开关.bat` from the root to quickly start or stop the panel.

Once started, open:

```text
http://127.0.0.1:1313
```

The panel shows Bridge and gateway status, manages the gateway through the official Hermes lifecycle commands, and edits the related profile files.

## Patches and backups

- `patches/hermes/kikka/SOUL.md` is the persona template deployed to the `kikka` profile. If an existing `SOUL.md` differs, it is backed up with a timestamp before replacement.
- `patches/taromati2/` contains the YAYA/ghost integration required for SSTP handlers, nurturance variables, persistent balloons, silence control, and related features.
- Before replacing different Taromati2 files, the script backs them up under local `patch-backups/<timestamp>/taromati2/`.
- Before the Bridge first adjusts local SSP/Taromati2 TTS settings, it backs them up under `patch-backups/<timestamp>/runtime/`. Other ghosts' YAYA settings are not modified.
- On later launches, an existing `kikka` profile containing `SOUL.md`, plus matching Taromati2 patch hashes, takes the script directly to the completed menu. The script does not compare the `SOUL.md` content hash, so post-deployment user customizations remain valid.
- The deployment menu can restore patch/runtime backups or the latest `SOUL.md` deployment backup, and saves the current targets before restoring.
- [See the detailed patch documentation here](patches/README_EN.md).

## Repository layout

| Path | Purpose |
| --- | --- |
| `自动部署脚本.bat` | Detects the environment, installs dependencies, and deploys the Hermes and Taromati2 patches |
| `控制面板开关.bat` | Starts/stops the local web control panel and displays its status |
| `scripts/auto_deploy.ps1` | PowerShell implementation used by the automatic deployment entry point |
| `hermes_bridge.py` | Main bridge runtime |
| `bridge_wrapper.py` | Bridge launcher wrapper |
| `hermes_input_modern.py` | Modern input UI |
| `bridge_control/` | Local web control panel |
| `scripts/` | Windows background launch, scheduled-task registration, and validation helpers |
| `patches/hermes/kikka/SOUL.md` | Persona template deployed to the Hermes `kikka` profile |
| `patches/taromati2/` | Taromati2 / YAYA integration patches |
| `requirements.txt` | Python dependency list |
| `.github/workflows/validate.yml` | Windows validation for the public tree, Python, and PowerShell |
| `SECURITY.md` | Local control-panel boundary and vulnerability reporting guidance |
| `LICENSE.md` | Mixed-license scope for repository code and third-party material |
| `THIRD_PARTY_NOTICES.md` | Notices for third-party projects, assets, and dependencies |
| `DISCLAIMER.md` | Risk notice for deployment, AI services, and third-party content |

## License and disclaimer

This is a mixed-license repository. The Bridge, control panel, and deployment
scripts maintained in this repository are available under the MIT License.
`patches/taromati2/**`, the Kikka `SOUL.md`, and `banner.png` are excluded from
the MIT grant and remain subject to the upstream Taromati2 non-commercial terms
and other applicable rights.

The modern input UI uses separately installed PySide6. PySide6 remains under
the applicable Qt for Python LGPLv3, GPLv3, or commercial terms; its binaries
are not redistributed in this repository.

See [LICENSE.md](LICENSE.md), [Third-Party Notices](THIRD_PARTY_NOTICES.md), and
the [Project Disclaimer](DISCLAIMER.md) for the complete scope and terms.
