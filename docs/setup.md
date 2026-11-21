# Setup

Installing Diatonic, voice, connecting GitHub and Google accounts, running it in the background, and fixing common problems. Back to the [README](../README.md).

## Quick start

Requirements: Python 3.11+ (3.12 recommended for voice), [uv](https://docs.astral.sh/uv/), and [Ollama](https://ollama.com).

```bash
ollama pull qwen3:4b-instruct

uv venv --python 3.12 .venv
uv pip install -p .venv -e ".[voice]"      # drop [voice] if you don't want voice
.venv/bin/diatonic --open                  # http://localhost:5005
```

Afterwards, `./diatonic.sh` starts the server from the project folder. `--port` is remembered in the config, so only pass it when you mean to change the port. To use a cloud model, add it in **Settings → Models** (see [Models](customizing.md#models-local-and-cloud-switch-on-the-go)).

The server binds to `127.0.0.1` only, and rejects requests with a foreign `Host` or `Origin` header (DNS-rebinding and cross-site protection).

To reach it through `tailscale serve` (or another proxy), keep the bind as it is and add the name the proxy forwards to `server.allowed_hosts` in `~/.diatonic/config.json`, for example `["box.tailnet.ts.net"]`. `["*"]` accepts any host name; a write must then still come from the same origin it was sent to. The app has no login, so anyone who can reach the proxy controls it. Restart the server after the change.

### Voice setup

```bash
.venv/bin/diatonic setup-voice             # or Settings → Voice → Download voice models
```

Downloads Whisper `small.en` (about 500 MB) and the Piper voice `en_US-lessac-medium` (about 60 MB) into `~/.diatonic/models/`. Piper's code is GPL-licensed; that doesn't matter for personal use, but check it if you redistribute.

- **Talk:** hold **⌥⇧Space** (rebindable in Settings → Voice), or click the mic and just speak. The transcript lands in the composer for review; turn on *Send voice input automatically* to skip that.
- **Spoken summary:** toggle it in the top bar. When a run finishes, the agent says one or two short sentences, only if something is worth saying: a run that used apps, needed approval, failed or was slow. Trivial chat stays silent, and the model can answer `NONE` to stay quiet. You can tune this under *When to speak*.
- If Piper isn't installed the app falls back to your system voice.
- Dictation always runs server-side Whisper, so it works the same in Firefox/Zen, Chrome and Safari. Browsers only ask for mic permission once for `localhost:5005`.

## Connecting apps

### GitHub
Settings → Apps → GitHub. Paste a personal access token with the `repo` scope, or leave it empty to reuse your `gh auth login` session. Optionally set a default owner so "my-repo" resolves to "owner/my-repo".

### Antigravity (Google)
Sign in once via `agy acp --login` in your terminal. In **Settings → Models**, select Antigravity to configure its model, thinking level, and **Permissions mode** (*Auto* for local-model level convenience where safe file edits and reads run automatically, or *Strict* to approve every action). In **Settings → Apps**, choose which Diatonic apps to share with Antigravity over local MCP.

### Google accounts (Calendar and Gmail, any number of accounts)
Settings → **Accounts** manages every Google account in one place; Calendar and Mail both use it.

1. In [Google Cloud Console](https://console.cloud.google.com/) create a project and enable the **Google Calendar API** and the **Gmail API**.
2. *OAuth consent screen*: set it up and add each Google account you'll connect as a **test user**.
3. *Credentials → Create credentials → OAuth client ID → **Desktop app*** (no redirect URI needed). If you choose *Web application* instead, add `http://localhost:5005/api/google/auth/callback` as an authorized redirect URI.
4. Paste the client ID and secret into Settings → Accounts once, then **Continue with Google** for each account. Choose per account what to allow: Calendar, and Gmail as *read only*, *read and manage* (archive, label, trash) or *read, manage and send*. You can reconnect with more or less access any time.
5. **Important:** while the consent screen's publishing status is "Testing", Google expires logins after **7 days**. Set it to "In production" (Google warns the app is unverified; that is fine for personal use) so accounts stay connected. If an account does expire, it is marked "needs reconnecting" with a one-click fix.

Give each account a label ("Work", "Home") and a colour. Reads (calendar, inbox) combine all enabled accounts; writes (creating an event, sending mail) go to the account you name, else your **default account**, else the agent asks which. Refresh tokens are stored in the macOS Keychain, never in the config file. An existing single-account Calendar login is adopted automatically.

Credentials and tokens are stored in the macOS Keychain (fallback: `~/.diatonic/secrets.json`, mode 0600).

## Keep it running in the background

Reminders and jobs only fire while the server runs. To run it at login and restart it if it crashes (macOS):

```bash
.venv/bin/diatonic install-service        # add --print to just see the launchd plist
.venv/bin/diatonic service-status
.venv/bin/diatonic uninstall-service
```

Logs go to `~/.diatonic/logs/`. If you run `.venv/bin/diatonic` by hand as well, stop it first, since both use port 5005.

## Troubleshooting

- **"Can't reach Ollama":** start it with `ollama serve` or open the Ollama app; check the endpoint in Settings → Models.
- **A cloud model fails:** press **Test** next to it in Settings → Models. It reports a missing or rejected key, an unknown model name, or rate limiting.
- **"request exceeds the available context size":** the model's context in Settings → Models is set higher than the model actually supports; lower it to match.
- **The interface broke after a change:** open `http://localhost:5005/?safe=1` to start with every agent-made change off, then undo it in Settings → Interface.
- **First reply is slow:** the model is loading into memory; it stays warm for 30 minutes afterwards.
- **Mic blocked:** click the permissions icon in the address bar and allow the microphone for `localhost:5005`.
- **Push-to-talk shortcut does nothing:** your browser may reserve the combination; click the field in Settings → Voice and press another.
- **`⌘K` opens the browser search bar:** the page overrides it, but if your browser doesn't allow that, use the command button in the top bar.
