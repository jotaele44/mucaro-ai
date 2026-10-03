# Phone Access

Use Mucaro from a phone on the same Wi-Fi or VPN as the computer that runs it. The computer
does the work and keeps the data; the phone is a browser window onto it.

Status: scope item `SD-012`, **PROVISIONAL**. The automated checks pass, but nobody has yet
observed a session on a physical iPhone over a real network (see [What is verified](#what-is-verified)).

## Turn it on

Phone access is off unless you ask for it. A normal launch listens on this computer only.

| How you start Mucaro | What to do |
|---|---|
| `Mucaro AI.app` (macOS) or `Mucaro AI.exe` (Windows) | Add `MUCARO_AI_PHONE_ACCESS=1` to `.env`, then open the app. A dialog offers to copy the link (a **Copy link** button on macOS, a Yes or No question on Windows). |
| `Mucaro AI.command`, `Mucaro AI.cmd` | Add `MUCARO_AI_PHONE_ACCESS=1` to `.env`, or pass `--phone` from a terminal. The window prints the link. |
| A terminal | `python scripts/launch_app.py --phone` |

Starting Mucaro with phone access:

- listens on all network interfaces, not just this computer;
- requires an access token. A token of 32 or more characters in `MUCARO_AI_OPERATOR_TOKEN` is
  used as it is; otherwise Mucaro makes a new random one for each launch;
- prints a link for every private network address it finds (home networks and VPNs):

```
PHONE_ACCESS_ENABLED bind=0.0.0.0 token=generated for this launch
PHONE_URL http://192.168.1.20:8000/#token=<the token>
PHONE_ACCESS_CLEARTEXT_HTTP this link is not encrypted: ...
```

If Mucaro is already running when you launch it with phone access, it says
`PHONE_ACCESS_UNAVAILABLE` and prints no link: it cannot know that older instance's token. Close
Mucaro and launch it again.

## Open it on the phone

1. Get the link to the phone, for example by AirDrop, Messages or Notes. It carries the token.
2. Open it in Safari. The token sits after the `#`, which browsers never send to the server, and
   Mucaro removes it from the address bar at once.
3. The link keeps the phone signed in for that browser tab. To stay signed in across tabs and
   restarts, open the plain address, paste the token on the **Enter the access token** screen
   and choose **Remember on this device**. Set a fixed `MUCARO_AI_OPERATOR_TOKEN` in `.env` so
   that the remembered token survives a restart of Mucaro.

Generate a token with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

## What protects it

| Control | Where |
|---|---|
| A public bind refuses to start unless the token has 32 or more characters, in every profile | `backend/app/security.py` |
| Every API route except `/health`, `/config`, `/security` and `/tools` needs the token | `backend/app/auth.py` |
| A request from another device is refused with `403 ERROR_REMOTE_ACCESS_REQUIRES_TOKEN` when no token is configured, which also covers a server started by hand with `uvicorn --host 0.0.0.0` | `backend/app/auth.py`, `backend/app/main.py` |
| Another device is not told the computer's file paths or allowed origins by `/health` or `/config` | `backend/app/main.py` |
| The token is compared in constant time, and it travels in a header, never in a URL path or query | `backend/app/auth.py`, `frontend/src/api.js` |
| The app's own window on the computer gets the token too, so nothing on the machine is left open | `scripts/launch_app.py` |
| The native apps (macOS and Windows) keep the token out of their log files. The macOS dialog gets the link through the environment, not the process arguments | `scripts/launch_app.py`, `scripts/native_window.py` |

The web page itself (HTML and scripts) is served to anyone who can reach the port. It contains no data.

Mucaro does not limit failed attempts. A random token of 32 characters or more is not guessable
this way, which is why a shorter one is refused.

**A proxy on the same computer hides who is calling.** If something on the Mac forwards to Mucaro
(a TLS terminator, a tunnel), every request arrives from the Mac itself and counts as local.
Always set a token when anything forwards to Mucaro. Phone access does this for you.

## Encryption

The default link is plain `http://`. On a network you do not trust, anyone on it can read what the
phone sends, including the token. Choose one of these:

- **A trusted home network.** Acceptable for personal use; the launcher and the app warn about it.
- **A VPN or overlay network** such as Tailscale or WireGuard. The launcher also lists addresses in
  `100.64.0.0/10`. The tunnel encrypts the traffic.
- **TLS.** Pass a certificate and key in PEM format to a terminal launch:
  `python scripts/launch_app.py --phone --tls-cert cert.pem --tls-key key.pem` (or the same options
  after `./"Mucaro AI.command"`). The macOS app and `.env` have no TLS setting. Mucaro does not
  create certificates. The phone must open the name the certificate was issued for, so replace the
  address in the printed link with that name. A self-signed certificate makes the phone show a warning.

## What a plain `http://` address cannot do

Browsers expose some features only to secure origins (`https://` or `localhost`). Over a plain
network address Mucaro still works, with these differences:

- `navigator.clipboard` does not exist, so **Copy** uses the older selection-based copy. If a
  browser refuses that too, Mucaro says the browser copies only over a secure connection.
- An installable web app and offline caching are unavailable. Mucaro does not ship either.

## If something is wrong

| On the phone | Meaning | What to do |
|---|---|---|
| The page never loads | The phone cannot reach the computer | Same network? Guest Wi-Fi and "client isolation" block it. The computer's firewall may be blocking it: macOS asks once whether Python (or Mucaro AI) may accept incoming connections, so allow it, or change it in System Settings, Network, Firewall. Windows usually asks whether to allow access on private networks. |
| **Phone access is turned off** | Mucaro answered but was started without phone access | Start it with phone access (see above) and open the new link |
| **Enter the access token** | This device has no token | Open the link, or paste the token |
| **That access token was not accepted** | A typo, or Mucaro was restarted and made a new token | Open the newest link, or paste the new token |

## What is verified

- The server checks run against a simulated remote peer in `backend/tests/test_phone_access.py`:
  the strong-token rule, the remote refusal, the redaction, the launcher plan, the TLS arguments
  and the native-app dialogs (with fakes for the macOS and Windows calls).
- The browser checks in `qa/rendered/rendered_qa.mjs` drive an emulated iPhone against a real
  backend, including a real non-loopback address of the test machine: the token gate, a changed
  token, remember-on-device, the cleartext notice, and Copy without the clipboard API.

Still open, and needing a person with a phone: signing in on a physical iPhone over a real Wi-Fi
network, the on-screen keyboard and the password field, resuming after the phone locks, the macOS
firewall prompt, and a network that isolates clients. The two native-app dialogs have not been run
on a real Mac or a real Windows PC either.

## Not supported

- An installable web app (PWA) or a native iPhone client (`SD-010`).
- Reaching the packaged, signed desktop app from another device. It listens on this computer only,
  and the `network_exposure` gate in `docs/release/SECURITY_GATES.md` checks that.
- QR codes, pairing codes, one token per device, or revoking a single device.
- IPv6-only networks: the launcher listens on IPv4 and lists IPv4 addresses.
- Exposing Mucaro to the internet.
