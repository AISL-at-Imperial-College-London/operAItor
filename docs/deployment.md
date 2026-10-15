# Deployment

The console is a Python server (plant simulation, agents, WebSocket) plus a static React build.
It needs a machine that runs it permanently; static hosts such as GitHub Pages or Vercel cannot run
the simulation.

## Your own Windows machine (the public site)

The public console runs on a **dedicated, always-on Windows machine**. It is installed there from
GitHub, never run from a development laptop. The install does not use or copy a development checkout,
a `.env` file or any keys, and the server never uses the machine's own LLM keys or models.

How it fits together:

| Piece | What it does |
|---|---|
| `deploy/windows/install.ps1` | Clones the repository into `C:\OperAItor\app`, creates a Python environment in `C:\OperAItor\venv` (on the server only), builds the console, and registers the startup task |
| Scheduled task `OperAItor` | Starts `start.ps1` at boot, whether or not anyone is logged in, without a stored password and without admin rights |
| `start.ps1` | Runs the server in public mode on `127.0.0.1:7860` and restarts it if it stops. Logs go to `C:\OperAItor\logs` and are kept 14 days |
| Tailscale Funnel | Publishes it at a fixed, free `https://<machine>.<tailnet>.ts.net` address. No domain, no router changes and no open ports are needed |

Public mode (`PLANT_HOSTED=1`):
- visitors connect their own LLM (free Groq, Gemini or OpenRouter keys; see below);
- nothing is written to disk;
- at most `MaxSessions` plants run at once.

### 1. Prepare the machine

- **Power:** Windows 10/11 or Windows Server, set to never sleep (Settings → System → Power: Sleep
  *Never*). After Windows Update restarts it, the server comes back on its own.
- **Software:** install
  - Python 3.12 from <https://www.python.org>, with the *py launcher* option ticked;
  - Git for Windows: <https://git-scm.com/download/win>;
  - Node.js 22 LTS: <https://nodejs.org>.
- **Account:** use an ordinary Windows account for the server. The installer needs an administrator
  PowerShell once to register the startup task, but the server itself runs with normal rights.
- **CPU:** each open browser tab runs its own plant on the server, about one CPU core while the plant
  runs fast. Set `-MaxSessions` to about the number of cores minus one.

### 2. Install

In an **administrator** PowerShell on the hosting machine:

```powershell
cd $env:TEMP
Invoke-WebRequest https://raw.githubusercontent.com/AISL-at-Imperial-College-London/operAItor/main/deploy/windows/install.ps1 -OutFile install.ps1
powershell -ExecutionPolicy Bypass -File .\install.ps1 -MaxSessions 4
```

It ends with `Running: version …`, and the console then answers at <http://127.0.0.1:7860> on that
machine. Options:
- `-InstallDir D:\OperAItor` installs somewhere else;
- `-Branch dev` installs a test branch;
- `-Port 8000` uses another port.

### 3. Publish it on the internet (Tailscale Funnel, free)

1. Install Tailscale on the hosting machine (<https://tailscale.com/download/windows>) and sign in.
   Use a lab or organisation account rather than a personal one.
2. In the Tailscale tray menu, open **Preferences** and turn on **Run unattended**. Without it,
   Tailscale stops when nobody is logged in.
3. In the admin console (<https://login.tailscale.com/admin>):
   - under **DNS**, enable **HTTPS certificates**;
   - under **Access controls**, allow Funnel for this machine. The first `tailscale funnel` command
     prints a link that does this.
4. In a PowerShell on the hosting machine:

   ```powershell
   tailscale funnel --bg 7860
   tailscale funnel status
   ```

   The status shows the public address, `https://<machine>.<tailnet>.ts.net`. `--bg` keeps the Funnel
   on across reboots.
5. Open that address from a phone on mobile data to check it works from outside. A new address can
   stay missing from public DNS until the machine has its HTTPS certificate. If it does not resolve
   after a few minutes, run `tailscale cert <machine>.<tailnet>.ts.net` once (in an empty folder; delete
   the two files it writes). The address appears seconds later.

   On an Imperial network, Tailscale Funnel works; Cloudflare's quick tunnels (`trycloudflare.com`)
   are blocked. Check Imperial ICT's rules before running a public service from a university machine.
6. To make the project page's **Open the live demo** button point there, set it in the GitHub
   repository: Settings → Secrets and variables → Actions → Variables → `DEMO_URL` = the address.
   It takes effect on the next Pages build.

Tailscale ends HTTPS on the machine and forwards to `127.0.0.1:7860`. The server is not reachable on
the network any other way.

### Running it

| Task | How |
|---|---|
| Status | `Invoke-RestMethod http://127.0.0.1:7860/api/health` (version and live sessions) |
| Logs | `C:\OperAItor\logs\operaitor-YYYYMMDD.log` |
| Update to the latest `main` | `powershell -ExecutionPolicy Bypass -File C:\OperAItor\update.ps1` (administrator) |
| Change settings | Edit `C:\OperAItor\settings.ps1` (port, sessions, timeouts), then `update.ps1 -Restart` |
| Stop / start | `update.ps1 -Stop` / `update.ps1 -Restart` (it also starts at every boot) |
| Take it offline | `tailscale funnel --bg 7860 off`. The server keeps running locally |
| Remove | `update.ps1 -Stop`, `Unregister-ScheduledTask OperAItor`, `tailscale funnel --bg 7860 off`, delete `C:\OperAItor` |

Do not put an `.env` file or API keys on this machine. Public mode ignores them, and visitors' keys
never reach it: their browsers call the providers directly (see below).

**With your own domain instead of a `ts.net` address:** use a Cloudflare named tunnel
(`cloudflared`, free with a domain on Cloudflare) pointing at `http://127.0.0.1:7860`. The rest of
the setup is unchanged.

## Hugging Face Spaces (needs a paid plan)

A Docker Space on **CPU basic** hardware (2 vCPU, 16 GB) runs the image as is and gives a public
`https://<owner>-<space>.hf.space` URL. The hardware has no hourly cost, but since 2026 creating a
Docker (or Gradio) Space needs a paid Hugging Face plan: PRO for a personal account, Team or Enterprise
for an organisation. Only static Spaces are free.
([Spaces overview](https://huggingface.co/docs/hub/spaces-overview), checked 15 Oct 2026.)

One-time setup:
1. Create the Space **under the AISL organisation on Hugging Face** (not a personal account) at
   https://huggingface.co/new-space, with SDK **Docker**, a blank template and the free hardware
   **CPU basic**.
2. In the GitHub repository, go to Settings → Secrets and variables → Actions:
   - add the secret `HF_TOKEN`: a Hugging Face access token with *write* access;
   - add the variable `HF_SPACE`: `<owner>/<space>`.
3. Push to `main`, or run the workflow **deploy to Hugging Face Space** by hand.

The workflow (`.github/workflows/deploy-hf-space.yml`):
- uploads only what the image needs (no git history, no benchmark results);
- replaces README.md with the Space card in `deploy/huggingface/README.md`, whose front matter sets
  `sdk: docker` and `app_port: 7860`.

The Space builds the image on each upload. It sleeps after 48 h without visitors and wakes on the
next visit (about 1 min).

## Render (free alternative)

New → Blueprint → select this repository. `render.yaml` defines a free Docker web service with a
health check. Free instances sleep after 15 min without traffic, and they have less CPU, so keep
`PLANT_MAX_SESSIONS` low.

## Anything that runs containers

```bash
docker build -t operaitor .
docker run -p 7860:7860 operaitor
```

Put HTTPS in front of it (browsers need it, and so do the visitors' provider calls), e.g. Caddy:
`reverse_proxy localhost:7860`. Health check: `GET /api/health`.

## Configuration (environment variables)

| Variable | Default (image) | Meaning |
|---|---|---|
| `PLANT_HOSTED` | `1` | Public mode. Only keys supplied by visitors are used; the server's `OPENAI_API_KEY`, local-model URLs and Ollama are ignored; nothing is written to disk |
| `PLANT_MAX_SESSIONS` | `4` | Concurrent plants (one per browser tab). Further visitors see "all sessions in use" |
| `PLANT_SESSION_IDLE_MIN` | `15` | A session with no open tab is closed after this long |
| `PLANT_SESSION_MAX_MIN` | `180` | Hosted mode: a session's maximum lifetime |
| `PLANT_ALLOWED_MODELS` | `gpt-5-nano,gpt-5-mini` | OpenAI models a visitor may choose (other providers: any model) |
| `PLANT_LLM_MODEL` | (auto) | Local runs only: default model. Unset: OpenAI if `OPENAI_API_KEY` is set, else local Ollama (`PLANT_OLLAMA_MODEL`, default `qwen3:8b`) |
| `PLANT_OLLAMA_URL` / `PLANT_OLLAMA_NUM_CTX` | `http://127.0.0.1:11434` / `16384` | Local Ollama server and context window (agents' prompts are 7–12k tokens) |
| `PLANT_AGENT` | (llm) | Default agent configuration for new sessions |
| `PLANT_CAMPAIGN_DIR` | (empty) | Where console campaign reports are written; empty = not written |
| `PORT` | `7860` | Listening port |

For a private deployment on a lab machine with a GPU, set `PLANT_HOSTED=0` and run Ollama next to
it: every session then uses the free local model. Protect such a deployment yourself (e.g. a
password on the reverse proxy).

## LLMs on the hosted site (free first)

The hosted site has no LLM of its own and pays for nothing. Each visitor connects one in
**Agents → Agent settings**:

| Provider | Cost to the visitor | Models (suggestions; any model name the provider accepts) |
|---|---|---|
| Groq | free tier | llama-3.3-70b-versatile, llama-3.1-8b-instant, qwen/qwen3-32b |
| Google Gemini | free tier | gemini-2.5-flash, gemini-2.5-flash-lite |
| OpenRouter | free models (`:free`) | meta-llama/llama-3.3-70b-instruct:free, qwen/qwen3-235b-a22b:free, … |
| Own Ollama | free | any model on their own computer or server; Ollama must allow the site (`OLLAMA_ORIGINS`) |
| OpenAI | billed to the visitor | gpt-5-nano, gpt-5-mini (`PLANT_ALLOWED_MODELS`) |

**Why there is no Ollama inside the Space:** the free Space hardware is CPU-only (2 vCPUs). A 3B
model there needs about 7 minutes per agent call, or roughly 20 minutes per decision, so a bundled
model would not be usable.

## Visitor keys: the server never receives them

In the public console the **visitor's browser makes every LLM call itself**. The agents still run on
the server, but each model call is handed to the visitor's tab, which calls the provider directly
and passes back the answer (`operaitor/agents/browser_llm.py`, `frontend/src/browserLlm.ts`).

```
server agents --llm_request (prompt, no key)--> visitor's browser --HTTPS + key--> Groq / Gemini / OpenRouter / OpenAI / Ollama
server agents <--llm_response (answer)--------- visitor's browser <-----answer----
```

- **The key stays in the tab.** It is held in the page's memory only, never in browser storage.
  Reloading or closing the tab forgets it.
- **The server cannot receive it.** A public server (`PLANT_HOSTED=1`) refuses any connection
  request that carries a key, so a key cannot reach it even by mistake.
- **The browser decides where the key goes.** The provider addresses are fixed in the page's own
  code, or typed by the visitor for Ollama. The browser never sends the key to an address the server
  names, so even a compromised server could not redirect it.
- **The browser checks the connection first.** It lists models, or uses OpenRouter's key endpoint,
  so no tokens are spent.
- **All four hosted providers allow calls from a browser:** Groq, Gemini, OpenRouter and OpenAI. A
  visitor's own Ollama must allow the site's address in `OLLAMA_ORIGINS`. Because the browser makes
  the call, Ollama on the visitor's own computer works (`http://localhost:11434`), and the server
  never connects to it.
- **The tab must stay open while the agents work.** If it closes, the session's pending calls fail
  cleanly and the plant keeps running.
- **Billing:** any cost is billed by the provider to the visitor's own account. The console shows
  calls and tokens per session, and the estimated cost for OpenAI.

A private deployment (`PLANT_HOSTED=0`) can still use the server's own `OPENAI_API_KEY` or a local
Ollama.
