# MC-Updater

MC-Updater is a small Windows updater helper for MiSTer Companion.

It checks the installed MiSTer Companion version from `config.json`, compares it with the latest GitHub release, downloads the newest Windows build when available, replaces the old `MiSTer-Companion.exe`, extracts the update, and removes the downloaded zip file afterward.

## Requirements

MC-Updater only works with **MiSTer Companion v4.0.8 or higher**.

Older versions of MiSTer Companion do not include the required MC-Updater support.

## Usage

Place `MC-Updater.exe` in the same folder as:

```text
MiSTer-Companion.exe
config.json
```

When MiSTer Companion detects that `MC-Updater.exe` is available, it can launch MC-Updater directly when a new update is found.

## Automatic Update Mode

MiSTer Companion creates an empty file named:

```text
updatenow.txt
```

MC-Updater detects this file on startup and begins the update process automatically.

After the update completes, MC-Updater removes `updatenow.txt` and shows a completion message.

Press **OK** to close the updater, then start MiSTer Companion again.

## Manual Mode

You can also run `MC-Updater.exe` manually and press **Check and Update**.

## Notes

MC-Updater is currently intended for Windows & Linux builds only.
