# Jarvis (local Windows voice assistant + optional iOS app)

A local, wake-word-driven voice assistant for Windows, powered by Claude.
Say "Hey Jarvis", speak a command, and it transcribes, thinks, acts (opens
apps, manages windows, controls volume, searches files, ...), and speaks
back — all voice processing runs locally on your machine; only your
transcribed text (not audio) is sent to Anthropic's API to generate a reply.

## The window

Launching `main.py` opens a real window (not just a tray icon) showing a
live conversation transcript, mic/listening status, connection info, and a
settings panel (gear icon, bottom of the left rail). You can talk to it by
saying "Hey Jarvis" as usual, by clicking the glowing orb at the bottom to
manually trigger one listen (skips the wake word), or by typing a command
into the text field above it and pressing Enter.

Closing the window (the small "x" in the top right, or Alt+F4) hides it to
the tray instead of quitting — Jarvis keeps listening in the background.
Restore it by double-clicking the tray icon or choosing "Show Jarvis" from
its right-click menu. Actually quitting is a separate, deliberate action:
the power icon in the window, or "Quit Jarvis" in the tray menu.

This window is built by loading real HTML/CSS/JS in a native window frame
(via `pywebview`, using Windows' built-in WebView2 engine) rather than a
traditional Python GUI toolkit — which is why it can look this polished
without hand-building every widget. See "The window won't open" under
Troubleshooting if it fails to appear.

## What it can do out of the box

- Open/close applications ("open notepad", "close spotify")
- List, focus, minimize, maximize, close windows by title
- Set/read system volume
- Take a screenshot
- Lock the workstation
- Search for and open files, read text file contents
- Open URLs / trigger a browser search
- Remembers recent conversation across turns (stored locally in
  `data/memory.json`)

It does **not** do real web search/browsing (only opens a browser tab),
does not integrate with email/calendar (not wired up), and does not
shut down/restart your PC (intentionally omitted — too risky for a
voice-triggered tool).

## Requirements

- Windows 10/11, with the **Microsoft Edge WebView2 Runtime** installed
  (this powers the app's window). It's preinstalled on Windows 11 and on
  most updated Windows 10 machines (it ships with Edge); if the window
  fails to appear, install it free from
  https://developer.microsoft.com/microsoft-edge/webview2/ (the
  "Evergreen Bootstrapper" is the one you want).
- **Python 3.11 (recommended) — NOT the newest release.** Get it from
  https://www.python.org/downloads/release/python-3119/ (scroll to
  "Windows installer (64-bit)"). This matters more than it sounds: several
  packages here (openwakeword, faster-whisper) depend on compiled
  libraries that don't publish prebuilt Windows binaries for
  brand-new Python versions right away. If you install "whatever's
  newest" (e.g. Python 3.13/3.14), you risk hitting the same
  "failed to build wheel" error repeatedly, once per package, as each one
  catches up. Python 3.11 is mature enough that everything here has
  prebuilt wheels for it. If you already have a newer Python installed
  system-wide, that's fine — just install 3.11 alongside it and use it
  specifically for this project's virtual environment (Step 2 below uses
  the `py -3.11` launcher for exactly this reason).
- A working microphone
- An Anthropic API key with billing enabled

## Setup

1. **Get a Claude API key.**
   Go to https://console.anthropic.com/, sign in, open **Settings → API
   Keys**, create a key, and add a payment method under **Billing** (the
   API is pay-as-you-go; a personal assistant used casually typically costs
   a few dollars a month).

2. **Install Python dependencies.**
   Open a terminal (PowerShell) in this project folder and run:

   ```powershell
   py -3.11 -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```

   (If `py -3.11` isn't recognized, you don't have Python 3.11 installed —
   install it from the link above first, then retry. `python -m venv venv`
   works too, but will use whatever Python version is currently your
   system default, which is the thing to avoid here.)

3. **Download the wake word and speech-to-text models** (one-time, ~1-2GB
   depending on the Whisper model size you pick in `config.yaml`):

   ```powershell
   python -m openwakeword.utils --download
   ```

   The first run of `main.py` will also auto-download the `faster-whisper`
   model the first time it's needed.

4. **Add your API key.**
   Easiest: just run `python main.py` (step 6 below) -- on first launch with
   no key set, Jarvis opens its Settings window automatically and lets you
   paste your key in there (saved straight to `.env`, no file editing).

   Or do it manually ahead of time: copy `.env.example` to `.env` and paste
   your real key in:

   ```powershell
   copy .env.example .env
   notepad .env
   ```

5. **(Optional) Review `config.yaml`.**
   Defaults are sane, but you may want to:
   - Lower `wake_word.threshold` if "Hey Jarvis" isn't triggering, or raise
     it if it triggers on background noise/TV.
   - Change `stt.model_size` to `"base"` or `"tiny"` if transcription is
     too slow on your machine (trade accuracy for speed), or `"medium"` if
     you have a beefy CPU/GPU and want better accuracy.
   - Edit `brain.system_prompt` to change Jarvis's personality/tone.
   - Run `python -m jarvis.audio.tts --list-voices` to see installed
     Windows voices and set `tts.voice_id` to pick a specific one.

6. **Run it.**

   ```powershell
   python main.py
   ```

   A tray icon (blue circle with a "J") appears. Say **"Hey Jarvis"**,
   wait a beat, then speak your command. It stops recording automatically
   once you pause. Right-click the tray icon to mute the mic or quit.

## Troubleshooting the window

- **The window never appears** (but the console shows "Jarvis is
  running" and no error): almost always a missing WebView2 Runtime — see
  Requirements above, install it, and retry. Less commonly, it opened
  behind another window or off-screen on a multi-monitor setup that's
  since changed — check the taskbar / Alt-Tab, or click "Show Jarvis"
  from the tray icon's right-click menu.
- **The window shows a blank white/black screen**: usually the page
  failed to load its assets. If you're running the plain
  `python main.py` version this shouldn't happen (the HTML files ship
  right in the repo); if you're running a built `.exe`, this points to
  `build_exe.bat`'s `--add-data` step not having bundled
  `jarvis\ui\assets` correctly — rebuild, and if it persists, paste the
  console output back.
- **Closing the window with the "x" doesn't quit the app**: that's
  intentional — see "The window" above. Use the power icon in the
  window, or "Quit Jarvis" from the tray menu, to actually exit.

## Troubleshooting install errors

- **`Failed to build installable wheels for ... webrtcvad`** (or any package
  with `failed-wheel-build-for-install`): that package has no prebuilt
  binary for your Python version and pip is trying to compile it from C
  source, which needs Microsoft's C++ Build Tools. This project already
  uses `webrtcvad-wheels` (a prebuilt drop-in replacement) to avoid this
  for VAD specifically. If you hit this for a *different* package, either
  look for a `-wheels` / `-binary` fork on PyPI, or install "Build Tools
  for Visual Studio" (Desktop development with C++ workload) from
  https://visualstudio.microsoft.com/visual-cpp-build-tools/.
- **`ModuleNotFoundError` right after a failed `pip install -r requirements.txt`**:
  when one package in the file fails to build, pip aborts and never installs
  the packages listed after it — that's why a single failure (like
  `webrtcvad`) can leave later ones (like `openwakeword`) missing too. Fix
  the failing package first, then re-run `pip install -r requirements.txt`
  to pick up everything that didn't get installed.
- Always re-run `pip install -r requirements.txt` after any individual fix
  — it's safe to run repeatedly; already-installed packages are skipped.

## Testing steps (do these in order after first setup)

1. `python -m jarvis.audio.tts --say "Testing, one two three"` — confirms
   TTS and your speakers/audio output work, independent of everything else.
2. `python -m openwakeword.utils --download` then run `python main.py` and
   watch the console: say "Hey Jarvis" and confirm you see a "Wake word
   detected" log line. If not, lower `wake_word.threshold` in
   `config.yaml` (e.g. 0.4) and retry.
3. After the wake word triggers, say a simple command like "what time is
   it" and confirm the console shows a "Transcribed: ..." log line with
   roughly correct text. If transcription is empty/garbled, check your
   microphone is the Windows default recording device and not muted.
4. Try a tool-using command: "open notepad" — Notepad should launch and
   Jarvis should say something confirming it.
5. Try "set volume to 50" and "what's the volume" to confirm the
   pycaw/system tools work.
6. Restart `python main.py` and say something referencing the prior
   conversation (e.g. "what did I just ask you to open?") to confirm
   `data/memory.json` persistence works across runs.
7. Confirm the window itself: type "hello" into the text field above the
   orb and press Enter — you should see it appear in the transcript and
   get a reply. Click the orb once and say a command out loud to confirm
   the manual-listen path works too (not just the wake word).
8. Click the gear icon (bottom of the left rail) to open Settings, move
   a slider, click Save, and confirm `config.yaml`'s comments are still
   intact afterward (open it in a text editor) — that verifies the
   settings save path isn't corrupting the file.
9. Close the window with the "x" (top right) and confirm Jarvis is still
   running (check the tray icon), then double-click the tray icon to
   confirm the window reappears.

## Project structure

```
main.py                     entry point: wiring + window + tray
config.yaml                 all tunables (wake word, STT, TTS, model, prompt)
.env                         your API key (create from .env.example, gitignored)
jarvis/
  config.py                  loads config.yaml + .env; save_settings() for the settings window
  audio/
    wake_word.py              openWakeWord listener (local, always-on)
    recorder.py                RMS-based silence-terminated recording
    stt.py                      faster-whisper local transcription
    tts.py                       pyttsx3 local speech output
  brain/
    claude_client.py           Claude tool-use loop
    memory.py                   JSON conversation history + facts
  tools/
    registry.py                 tool schemas + dispatch table (also tracks recent calls for the UI)
    apps.py, windows_control.py, system.py, files.py, web.py
  ui/
    window.py                   pywebview window + Python<->JS bridge (JarvisAPI, SettingsAPI)
    assets/main.html             the main window's HTML/CSS/JS
    assets/settings.html         the settings window's HTML/CSS/JS
  tray.py                      system tray icon (show / mute / quit)
data/                        created at runtime: memory.json, logs, screenshots
```

## Extending it

Adding a new capability is 3 steps:
1. Write a function that does the thing (e.g. in a new `jarvis/tools/media.py`).
2. Add a tool schema for it to `TOOL_SCHEMAS` in `jarvis/tools/registry.py`.
3. Add a matching entry to `TOOL_DISPATCH` in the same file.

Claude will then be able to call it whenever it's relevant to a request —
no changes needed to the brain or main loop.

Ideas for next steps: real web search (Brave Search / Tavily API), email
and calendar (Microsoft Graph API for Outlook, or IMAP), smart home
control (Home Assistant REST API), swapping local TTS for a cloud voice
(ElevenLabs) for a more natural sound, or running as a proper Windows
service (via NSSM or Task Scheduler) so it starts automatically at login.

## Building a standalone .exe

Want to run Jarvis without opening a terminal / activating a venv every
time (e.g. a double-clickable app, or a Startup shortcut)? See
`BUILD_EXE.md` for packaging this into `Jarvis.exe` with PyInstaller. Do
this only after the normal `python main.py` setup above is fully working.

## Building an installer (for sharing with other machines)

Want a single `JarvisSetup.exe` that installs Jarvis on another PC with no
Python and no manual file editing? See `installer/README.md` -- it packages
the built `.exe` above with Inno Setup, auto-installs the WebView2 Runtime
if needed, and adds Start Menu/Desktop shortcuts and an uninstaller. The API
key is still entered once, in-app, on first launch.

## iOS app

There's also a companion iOS app (`ios-app/`) that talks to this same
brain over your local network / Tailscale — same memory, same PC-control
tools, just a push-to-talk phone interface instead of a wake word (Apple
doesn't allow always-listening background apps). See `ios-app/README.md`
for the full setup, which requires a Mac with Xcode to build.

This project's `server.py` (the local API the iOS app talks to) is enabled
by default (`server.enabled: true` in `config.yaml`) — if you don't plan
to use the iOS app, you can set it to `false` and skip generating an
API token.

## Known limitations

- Wake word / STT / TTS are all local and free, which means lower audio
  quality and higher latency than a cloud pipeline (ElevenLabs/cloud
  Whisper) — expect ~1-3 seconds of "thinking" time after you finish
  speaking, and an occasionally robotic voice.
- No noise cancellation — a noisy room will hurt both wake-word accuracy
  and transcription accuracy. Use a headset mic if possible.
- Runs in the foreground (or from a console); it is not yet installed as a
  Windows service/startup item. Add it to your Startup folder
  (`shell:startup`) as a shortcut to `main.py` (or a small `.bat` that
  activates the venv and runs it) if you want it running on login.
- Single-user, local-only: there's no auth, no remote access, and the tool
  layer trusts whatever Claude decides to call. Don't expose this machine
  to untrusted audio input (e.g. a smart speaker in a shared space) since
  anyone who can be heard by the mic can issue commands.
