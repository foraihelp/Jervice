# Building JarvisSetup.exe

This produces a single installer that anyone can run on a fresh Windows
machine -- no Python, no manually copying `.env`. It installs the app,
installs the Microsoft Edge WebView2 Runtime automatically if the target
machine doesn't already have it, creates Start Menu / optional Desktop
shortcuts, and adds a normal Windows uninstaller entry.

Do this **after** `build_exe.bat` (see `../BUILD_EXE.md`) has produced
`dist\Jarvis\`. The installer just packages that folder -- it doesn't build
the app itself.

## One-time setup

1. Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) (free).
2. Download the WebView2 Evergreen Bootstrapper (small, ~2MB) into this
   folder -- it's fetched at build time rather than committed to the repo:

   ```powershell
   curl.exe -L -o MicrosoftEdgeWebview2Setup.exe "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
   ```

   This is Microsoft's own redistributable installer; the `.iss` script only
   runs it if it detects the runtime isn't already present on the target
   machine (checked via the registry), so most users won't need it at all.

## Build

```powershell
& "C:\Users\<you>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" jarvis.iss
```

Output: `installer\output\JarvisSetup.exe` (not committed to git -- see
`.gitignore` -- rebuild it locally or grab it from wherever it was
distributed).

## What it does NOT include

No `.env` is packaged or created at build time. The app itself creates an
empty one on first launch (see `jarvis/config.py:load_config`) and the main
window prompts the user to paste their Claude API key into Settings --
nobody needs to hand-edit a file to get started.
