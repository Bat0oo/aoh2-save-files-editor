# AoH2 Save Editor (Desktop)

A free, open-source save editor for **Age of History II** (also known as
**Age of Civilizations II**). It opens the game's save files — which are stored
in a binary Java format you can't edit by hand — and shows them as a readable,
editable tree: money, civilization names, province ownership, diplomacy,
technology, and everything else the game stores.

> Prefer not to install anything? There's a browser version that does the same
> thing (nothing to download, works on any device): **[placeholder]**

## What you can do

- **Add money** — set your civilization's treasury to anything.
- **Rename civilizations** — edit the name and tag (long and non-Latin names,
  e.g. Cyrillic, work fine).
- **Transfer provinces / annex a country** — change who owns a province, or use
  **Batch Edit** to move every province of one civ to another in a single click.
- **End stuck wars** — when the game won't let you make peace, edit the
  relationship value that drives the war.
- **Edit anything else** — technology, research, diplomacy points, population,
  and any other value the game wrote.

## Download

Grab `AoH2SaveEditor.exe` from the [latest release](../../releases/latest)
(Windows). It's a single portable program — no installer.

Windows SmartScreen may warn about an unrecognized app (normal for small,
unsigned indie tools). Choose **More info → Run anyway**, or build it yourself
from source (below).

## How to use

1. Save in-game, then **fully exit** Age of History II.
2. Open the editor → **File → Open Project Folder** → select your save folder,
   usually:
   `…\steamapps\common\Age of Civilizations II\saves\games\<map>\<your save>\`
3. Use the **Filter** box (tag like `ser2`, or `field=value` like
   `iTrueOwnerOfProvince=1`) and double-click values to edit. Use **Batch Edit**
   to change many objects at once.
4. **File → Save Modified Files** (automatic timestamped backups are kept in a
   separate folder).
5. Load the save in-game.

## Run from source (instead of the exe)

Requires Python 3.12+.

```bash
pip install javaobj-py3
python aoh2_editor.py
```

## Build the exe yourself

```bash
build.bat
```

Produces `dist\AoH2SaveEditor.exe`.

## Safety

The editor never overwrites your originals without keeping a timestamped backup,
and it only writes when you choose Save. Still, keep your own backup of a save
folder before editing, as with any save tool.

## License

MIT — see [LICENSE](LICENSE).
