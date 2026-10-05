# Deadlock Build Editor

A lightweight Qt GUI for editing Deadlock build box dimensions and exporting verified KV3 cache files.

## Requirements

- Python 3.10+ with [PySide6](https://pypi.org/project/PySide6/)
- [.NET 10 SDK](https://dotnet.microsoft.com/download/dotnet/10.0)

The GUI currently expects this source layout:

```text
KV3GUI.py
Kv3Tool/
├── Kv3Tool.csproj
└── Program.cs
```

## Run

With [uv](https://docs.astral.sh/uv/):

```powershell
uv run --with PySide6 --python 3.13 --no-project python KV3GUI.py
```

Or with an existing Python installation:

```powershell
python -m pip install PySide6
python KV3GUI.py
```

The .NET helper builds automatically on first use; initial setup requires internet access.

## Usage

1. **Open** a copy of `cached_hero_builds.kv3`.
2. Select a build. Edit each box's width (middle field) and height (right field), or use **+10 width this build**.
3. **Reset this build** restores its original dimensions.
4. **Export** to a new KV3 file. Exports are verified; existing files are never overwritten.

The cache is typically located at:

```text
<Steam>/userdata/<account ID>/1422450/remote/cfg/cached_hero_builds.kv3
```

**Back up your cache and close Deadlock before replacing it.** This is an offline editor, not a live in-game tool. Publishing still happens through the game, and **Compact All** may recalculate edited dimensions.

Powered by [ValveResourceFormat](https://github.com/ValveResourceFormat/ValveResourceFormat). Not affiliated with Valve.
