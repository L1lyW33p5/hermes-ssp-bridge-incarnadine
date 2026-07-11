# Hermes and Taromati2 integration patches

<p>
  <a href="README.md">简体中文</a> |
  English
</p>

This directory contains the small Taromati2/YAYA `.dic` files that were changed for `hermes-ssp-bridge` integration.

It intentionally does not contain the full SSP installation, the full Taromati2 ghost, shell assets, profile state, logs, caches, or personal `aya_variable.cfg` values.

Use the repository-root `自动部署脚本.bat` to apply these files after its Hermes and SSP/Taromati2 checks pass. Existing target files are backed up before replacement and every copied file is verified with SHA-256.

## Included patch files

- `ghost/master/dic/system/event_response.dic` — bridge SSTP handlers such as `OnGetNurturance`, `OnSetNurturance`, and `OnKikkaBalloon`.
- `ghost/master/dic/aya/master/shiori.dic` — bridge lock / silence integration around YAYA event routing.
- `ghost/master/dic/system/anti_cheat.dic` — persistence hooks for selected bridge-controlled variables.
- `ghost/master/dic/nurturance/nurturance.dic` — nurturance value protection used by bridge-controlled values.
- `ghost/master/dic/system/clock.dic` — local event behavior adjusted for the bridge runtime.
- `ghost/master/dic/system/menu.dic` — local menu/runtime integration touched by the bridge setup.

## License

This directory is not uniformly licensed under MIT. Taromati2/YAYA-derived
files remain subject to the upstream non-commercial terms. See
[`taromati2/LICENSE.md`](taromati2/LICENSE.md) and the repository-level
[`LICENSE.md`](../LICENSE.md).
