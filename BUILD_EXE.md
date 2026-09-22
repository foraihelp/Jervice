# Building Jarvis.exe (standalone, no Python needed to run it)

This packages the app + all its Python dependencies into a folder you can
double-click to run, without anyone needing Python installed. You still
need Python **to build it** — this doesn't remove that requirement, it
just means end users (or "future you" on a different PC) won't need it.

Important: I can't build or test this .exe myself — there's no Windows
machine in my sandbox, and PyInstaller output is platform-specific (a
Linux-built package will not run on Windows, so I genuinely cannot produce
a working .exe on your behalf, only the build tooling to make yours). This
guide is a solid, standard PyInstaller setup, but treat the first build as
something to verify, not something pre-tested.

## Before you start

Do this **after** your normal setup from README.md is fully working (i.e.
`python main.py` runs correctly, wake word / STT / TTS all work). Building
an exe on top of a broken setup just gives you a broken exe that's harder
to debug. In particular, make sure you've already run
`python -m openwakeword.utils --download` — the build step bundles
whatever model files are already sitting in your venv, so if they weren't
downloaded first, the exe will be missing them.

## Build steps

1. In your activated venv, install the extra build-only dependency:

   ```powershell
   pip install -r requirements-build.txt
   ```

2. Run the build script from the project's root folder (same folder as
   `main.py`):

   ```powershell
   build_exe.bat
   ```

   This takes a few minutes. It bundles Python + all dependencies
   (including the ML libraries — onnxruntime, ctranslate2, numpy — so
   expect the output to be **large, likely 500MB-1.5GB**; that's normal
   for a bundled speech/ML app, not a sign something went wrong).

3. When it finishes, your app is in `dist\Jarvis\`. `config.yaml` is copied
   there automatically by the script, as a template -- the app copies it
   into `%LOCALAPPDATA%\Jarvis\config.yaml` (a location any user can write
   to, unlike e.g. Program Files) the first time it runs, and creates its
   own `.env` there too. Run it with:

   ```powershell
   dist\Jarvis\run_jarvis.bat
   ```

   Use `run_jarvis.bat`, not `Jarvis.exe` directly, at least for this first
   run — double-clicking an .exe makes Windows close its console the
   instant the process exits, so if it crashes on startup you'd never see
   why (the window just flashes and disappears). `run_jarvis.bat` is a
   small wrapper the build script generates that keeps the window open
   afterward either way, crash or clean exit, so you can actually read
   what happened. Once you've confirmed it starts up fine, running
   `Jarvis.exe` directly (or pinning it, or a Startup shortcut to it) is
   fine.

   The build is also intentionally `--console` (shows a black terminal
   window alongside the tray icon) for this first run, so Python errors
   are visible at all. Once it's working end-to-end, see "Going windowed"
   below to hide that console.

## Distributing / moving it

`dist\Jarvis\` is a self-contained folder — copy the *whole folder* (not
just the .exe) to move it, e.g. to another PC, a USB drive, a Startup
shortcut, or Program Files via the installer (see `installer/README.md`).

Your actual settings, API key, and conversation memory do **not** live in
this folder — they live under `%LOCALAPPDATA%\Jarvis\` (config.yaml, .env,
data\), a location that's always writable by the current user regardless of
where `Jarvis.exe` itself ends up (including read-only-to-standard-users
locations like Program Files). That folder is created and filled in
automatically the first time the app runs, so sending someone this
`dist\Jarvis\` folder never risks leaking your API key — there's nothing
personal in it to strip out.

## Going windowed (hide the console)

Once you've confirmed the console build works correctly end to end, open
`build_exe.bat` and change `--console` to `--windowed` on the PyInstaller
command, then re-run `build_exe.bat`. This hides the black terminal
window, leaving just the tray icon. Keep a console build around (or
switch back temporarily) if you ever need to debug an issue, since
`--windowed` swallows error output.

## Troubleshooting

- **Double-clicking Jarvis.exe flashes a black window that immediately
  closes**: this is Windows closing the console the moment the process
  exits — almost always because it crashed on startup (commonly a corrupted
  `%LOCALAPPDATA%\Jarvis\config.yaml`, or a `config.yaml` template missing
  next to the exe on a first run that needs it) and you're seeing it
  exit, not launch successfully. Run `dist\Jarvis\run_jarvis.bat` instead
  (see step 3 above) — it keeps the window open afterward so you can
  actually read the error, whether that's a Python traceback or just
  "Jarvis has exited." If you already double-click-ran it once and it's
  gone, that's fine, nothing's corrupted — just switch to
  `run_jarvis.bat` and try again.
- **The built app shows a blank white/black window, or the window never
  appears at all**: two likely causes. (1) The target machine is missing
  the Microsoft Edge WebView2 Runtime (see README.md Requirements) --
  install it. (2) `build_exe.bat`'s `--add-data "jarvis\ui\assets;jarvis\ui\assets"`
  step didn't bundle the HTML files correctly -- confirm
  `dist\Jarvis\_internal\jarvis\ui\assets\main.html` actually exists after
  a build; if it's missing, re-run `build_exe.bat` from the project root
  (it must find `jarvis\ui\assets` relative to where you run it).
- **Windows SmartScreen says "Windows protected your PC" when you run
  Jarvis.exe**: expected — the exe isn't code-signed (that requires a paid
  certificate), so Windows doesn't recognize the publisher. Click "More
  info" → "Run anyway". This is a one-time prompt per build.
- **Antivirus flags or quarantines Jarvis.exe**: also common and expected
  for unsigned PyInstaller executables — this is a well-known false
  positive class (PyInstaller's bootloader pattern resembles some malware
  packers to heuristic scanners), not a sign your build actually has a
  virus. Add an exclusion for `dist\Jarvis\` in your antivirus, or submit
  it to your AV vendor as a false positive if this bothers you.
- **`ModuleNotFoundError` or `DLL load failed` when running Jarvis.exe**:
  PyInstaller missed a dependency. Rebuild with `--console` (if you'd
  switched to `--windowed`) to see the actual missing module name, then
  add `--hidden-import <that module>` (or `--collect-all <that package>`
  for a package with data files) to the command in `build_exe.bat` and
  rebuild.
- **Wake word doesn't trigger / STT seems to be missing its model** in the
  built exe but worked fine under `python main.py`: you likely built
  before running the model download step, or `--collect-all openwakeword`
  didn't pick up the resource files. Re-run
  `python -m openwakeword.utils --download` in your dev venv, then rebuild.
- **Build itself fails partway through**: read the actual error near the
  top of the failure, not just the last line — PyInstaller's own errors
  are usually specific about which package/file it couldn't handle. Paste
  it back to me and I'll adjust `build_exe.bat`.
- **It works but takes a while to launch**: normal for a `--onedir`
  PyInstaller build with these ML dependencies — a few seconds of startup
  while it loads the Whisper/wake-word models into memory is expected,
  not a bug.

## Why `--onedir` instead of `--onefile`

`--onefile` produces a single .exe that's more convenient to move around,
but it works by silently unzipping itself into a temp folder every single
time it launches — with dependencies this large (onnxruntime,
ctranslate2), that adds real startup delay on every launch and can trip
antivirus scanners even harder. `--onedir` (a folder of files) starts
faster and is what this build script uses. If you specifically want a
single-file exe anyway, change `--onedir` to `--onefile` in
`build_exe.bat` — everything else about the command still applies.
