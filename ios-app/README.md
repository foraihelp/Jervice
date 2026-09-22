# Jarvis iOS app (push-to-talk client)

This is a thin client: it does NOT run its own copy of the assistant. It
records your voice, transcribes it on-device, sends the text to the Jarvis
server running on your Windows PC (`jarvis/server.py`, part of the main
project), and speaks back whatever reply the PC's brain sends. This is what
makes it "the same Jarvis" as the Windows app — same memory, same tools,
same PC-control abilities (yes, you can say "open Chrome" from your phone
and it happens on your PC).

## Requirements

- A Mac with Xcode 15+ installed (Xcode is required to build iOS apps —
  there is no way around this, Apple doesn't allow building/signing iOS
  apps on Windows or Linux)
- An iPhone (or the iOS Simulator, for testing without a physical device —
  though the simulator's microphone support is limited, a real device is
  recommended)
- A free Apple ID is enough to build and run on your own device for
  development (no paid Apple Developer account needed unless you want to
  distribute the app beyond your own phone)
- Your Windows PC from Part 1, with `server.enabled: true` in
  `config.yaml` (it is, by default) and `JARVIS_API_TOKEN` set in `.env`

## Step 1: Let your iPhone reach your Windows PC (Tailscale)

Your phone needs a way to reach the server running on your PC, securely,
even when you're not on the same Wi-Fi (e.g. out and about on cellular).
The simplest safe way to do this without port-forwarding your router (which
would expose your PC to the public internet) is **Tailscale** — a free,
personal-use VPN mesh that gives each of your devices a private address
only reachable by your other devices.

1. On your Windows PC: install Tailscale from https://tailscale.com/download
   and sign in (Google/Microsoft/GitHub account — whatever's easiest).
2. On your iPhone: install the "Tailscale" app from the App Store and sign
   in with the **same account**.
3. On your PC, open the Tailscale app and note the device's Tailscale name
   — it looks like `jarvis-pc.tailXXXXX.ts.net` (or check
   https://login.tailscale.com/admin/machines).
4. Your PC's Jarvis server address is then:
   `http://<that-tailscale-name>:8731`
   (8731 is the default port from `config.yaml` — change both if you edit it).
5. Test it works: on your iPhone, open Safari (with Tailscale running) and
   visit `http://<tailscale-name>:8731/health` — you should see
   `{"status":"ok"}`. If that doesn't load, Tailscale isn't connecting or
   `main.py` isn't running on the PC — fix that before touching Xcode.

Windows Firewall may prompt you to allow Python/uvicorn to accept
connections the first time `main.py` runs with the server enabled — allow
it for private networks.

## Step 2: Create the Xcode project

1. Open Xcode → **File → New → Project**.
2. Choose **iOS → App**, click Next.
3. Product Name: `Jarvis`. Interface: **SwiftUI**. Language: **Swift**.
   Leave Core Data/Tests unchecked (not needed).
4. Save it anywhere you like.
5. In the Project Navigator, **delete** the default `ContentView.swift` that
   Xcode generated (move to trash), then drag all the `.swift` files from
   this `ios-app/Jarvis/` folder into the Xcode project (check "Copy items
   if needed" and add to the `Jarvis` target).

## Step 3: Add required permissions (Info.plist)

iOS requires you to declare *why* the app uses the microphone and speech
recognition, or it will crash instantly when requesting permission. In
Xcode, select the project → your target → **Info** tab → add these two
rows (or open `Info.plist` as source and add the raw keys):

| Key | Type | Value |
|---|---|---|
| `Privacy - Microphone Usage Description` (`NSMicrophoneUsageDescription`) | String | "Jarvis needs the microphone to hear your voice commands." |
| `Privacy - Speech Recognition Usage Description` (`NSSpeechRecognitionUsageDescription`) | String | "Jarvis transcribes your speech on-device to send your commands to your assistant." |

## Step 4: Build and run

1. Plug in your iPhone (or pick a Simulator) as the run destination in
   Xcode's toolbar.
2. If running on a physical device: select your project → **Signing &
   Capabilities** → choose your Apple ID under Team (Xcode will offer to
   let you add a free personal team if you don't have one).
3. Press ▶ Run. On first launch, it'll ask for microphone and speech
   recognition permission — allow both.
4. Tap the gear icon (top right) → enter your PC's server URL
   (`http://<tailscale-name>:8731`) and the API token from your PC's
   `.env` file (`JARVIS_API_TOKEN`) → Done.
5. Press and hold the mic button, speak your command, release. It sends
   the transcribed text to your PC, waits for Jarvis's reply, shows it in
   the chat log, and speaks it back.

## Testing steps

1. `/health` check in Safari (Step 1.5 above) — confirms networking works
   before you even open Xcode.
2. Launch the app, grant permissions, hold the mic button and say
   "hello" — release and confirm the transcript preview showed something
   close to what you said before it was sent.
3. Confirm you get a spoken + on-screen reply back within a few seconds.
   If you get "Server rejected the request," your token doesn't match —
   re-check `.env` on the PC vs. Settings on the phone (no extra spaces).
4. Try a PC-control command from your phone, e.g. "take a screenshot" —
   then check `data/screenshots/` on the PC to confirm it actually ran
   there, not just replied conversationally.
5. Turn off Wi-Fi on your phone (use cellular only) and repeat step 4 to
   confirm Tailscale is actually working remotely, not just on local Wi-Fi.

## Known limitations

- No "Hey Jarvis" wake word on iOS — Apple does not allow apps to run a
  background always-listening microphone (this would be rejected from the
  App Store and isn't achievable via public APIs). Push-to-talk is the
  supported pattern here; a "Hey Siri, ask Jarvis..." Shortcuts integration
  is a possible future addition but requires separate App Intents work.
- The app must stay in the foreground while recording (standard iOS mic
  behavior) — you can't record while the phone is locked or you've
  switched apps.
- If your Windows PC is asleep/off, or `main.py` isn't running, the app
  will fail to connect — there's no cloud fallback; the PC has to be on.
- I was not able to compile or run this Swift code myself (no Mac/Xcode in
  this sandbox), so treat this as a solid, complete first draft rather
  than pre-verified — if Xcode reports a build error, paste it back to me
  and I'll fix it directly.
