# Pitchlight Chrome extension

Start Pitchlight from inside any browser meeting: Google Meet, Microsoft Teams on the web, Zoom on the web, Webex,
or any other tab. The extension captures that tab's audio, so the coach hears the other person without the
"Choose meeting audio" share dialog. You keep hearing the meeting as normal.

Your own voice still comes from the microphone in the Pitchlight meeting page, and all the coaching (notch, answers,
recap) is the existing `/meeting` page. The extension only provides the meeting audio and a one-click start.

**Not covered:** the Zoom and Teams desktop apps. Chrome can't hear other apps, so join those calls in the browser
(both offer "join from your browser"). Covering desktop apps would need a separate desktop app.

## Install (pilot, no Chrome Web Store)

1. Make sure Pitchlight is running (`docker compose up -d --build`, then http://localhost:8080 opens).
2. In Chrome, open `chrome://extensions` and turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose this `extension/` folder.
4. Pin it: click the puzzle-piece icon in the toolbar, then the pin next to **Pitchlight meeting coach**.

After you change files in `extension/`, press the reload arrow on the extension's card in `chrome://extensions`.

## Use it

1. Join the meeting in a Chrome tab.
2. On that tab, click the Pitchlight icon and press **Start coaching on this tab**, or press **Alt+Shift+P**
   (**⌥⇧P** on a Mac). The icon shows a red **ON** badge while it listens.
3. Pitchlight's meeting page opens in a small window beside the meeting (or focuses the one you already have open).
   Step 3 there says *Connected through the Pitchlight extension*. Press **Start meeting coach**.
4. End the meeting in Pitchlight and the extension stops listening. Closing the meeting tab or pressing **Stop
   listening** in the popup also stops it, and the coach carries on with your voice only.

You can also start the coach first and click the extension later: the coach picks up the other person as soon as
the extension starts.

Tell the people on the call that an AI coach is transcribing it. Some places require everyone's consent.

**Settings** (popup → Settings): the Pitchlight server address (default `http://localhost:8080`; any other address
asks Chrome for permission to reach it, for example the future company subdomain) and whether Pitchlight opens in a
small window or a new tab. Change the shortcut at `chrome://extensions/shortcuts`.

## How it works

```
meeting tab ──tabCapture──▶ offscreen.html ──16 kHz PCM, 100 ms──▶ background.js ──port──▶ bridge.js
 (Meet, Teams…)              plays the audio back                   (relay, state,         (content script on
                             to you + pcm-worklet.js                 badge, window)         the Pitchlight page)
                                                                                                 │ postMessage
                                                                                                 ▼
                                    frontend/js/ext-bridge.js rebuilds a MediaStream ──▶ meeting.js treats it
                                    like a shared tab: leveller → 0x06 frames → server transcribes "Them"
```

| File | Job |
|---|---|
| `manifest.json` | MV3 manifest. Permissions: `tabCapture`, `offscreen`, `activeTab`, `storage`, `scripting`; host access to localhost only (other servers are optional permissions). |
| `background.js` | Service worker: start/stop, offscreen document, relays audio to Pitchlight pages, badge, opens or focuses `/meeting`, keyboard shortcut, content script for a custom server. `globalThis.pitchlight` exposes `startCapture` / `stopCapture` / `getCapture` for the worker console. |
| `offscreen.js`, `pcm-worklet.js` | Turn the capture into audio, keep it audible (Chrome mutes a captured tab otherwise), downmix and resample to 16 kHz mono 16-bit, 100 ms chunks. |
| `bridge.js` | Content script on the Pitchlight server's pages only. Relays chunks and status to the page; carries `stop_audio` / `meeting_ended` back. |
| `popup.*`, `options.*`, `ui.css`, `settings.js` | Toolbar popup, settings page, shared styles and settings. |

Page side (served by the Pitchlight server, so it works with or without the extension):
`frontend/js/ext-bridge.js` (`window.PLExt`) and a few lines in `frontend/js/meeting.js` (`otherFromExt`,
`renderExtStatus`). The server is unchanged: the audio arrives as normal `0x06` meeting-audio frames.

Chrome only allows tab capture after the user invokes the extension on that tab (popup or shortcut). That is why the
start button lives in the extension and not on the meeting page.

## Test

`scripts/extension_e2e.js` loads the extension in headless Chromium, plays three practice-client clips in a fake
meeting tab and checks that the server transcribes them as the other person, that the meeting stays audible, and that
ending the meeting stops the extension. Instructions are at the top of the script. It needs a throwaway server on
port 8091 and uses only the free Gemini key.

## Publishing later

For colleagues, either share this folder (Load unpacked) or publish it to the Chrome Web Store: that needs a one-time
$5 developer registration, a privacy policy, and a justification for each permission. Before that, point the default
server address at the company deployment.
