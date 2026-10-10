# Deployment

The console is a Python server (plant simulation, agents, WebSocket) plus a static React build,
shipped as one Docker image. It needs a host that runs a container permanently. Static hosts such
as GitHub Pages or Vercel cannot run the simulation.

## Hugging Face Spaces (free, recommended for demos)

A Docker Space on the free **CPU basic** hardware (2 vCPU, 16 GB) runs the image as is and gives a
public `https://<owner>-<space>.hf.space` URL.

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

Put HTTPS in front of it (the key travels over the WebSocket), e.g. Caddy:
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
**Assistant → Connect an LLM**:

| Provider | Cost to the visitor | Models (suggestions; any model name the provider accepts) |
|---|---|---|
| Groq | free tier | llama-3.3-70b-versatile, llama-3.1-8b-instant, qwen/qwen3-32b |
| Google Gemini | free tier | gemini-2.5-flash, gemini-2.5-flash-lite |
| OpenRouter | free models (`:free`) | meta-llama/llama-3.3-70b-instruct:free, qwen/qwen3-235b-a22b:free, … |
| Own Ollama | free | any model on their server; it must be reachable over https from the internet, e.g. through a tunnel |
| OpenAI | billed to the visitor | gpt-5-nano, gpt-5-mini (`PLANT_ALLOWED_MODELS`) |

**Why there is no Ollama inside the Space:** the free Space hardware is CPU-only (2 vCPUs). A 3B
model there needs about 7 minutes per agent call, or roughly 20 minutes per decision, so a bundled
model would not be usable.

## Visitor keys and addresses: what happens to them

- **Sending:** the key (and, for Ollama, the address) goes from the browser to the server over the
  WebSocket. Use HTTPS; Spaces and Render provide it.
- **Checking:** the server checks the connection with a call that spends no tokens (listing models,
  or OpenRouter's key endpoint).
- **Ollama addresses:** a visitor's Ollama address must be https and must not resolve to a private,
  loopback or link-local address, so the public server cannot be used to reach internal networks.
- **Use:** the server builds that session's agents with the connection. The key is held in process
  memory only and is never written to disk or logs.
- **Isolation:** it is never sent back to the browser, and other sessions cannot use it.
- **End of session:** it is dropped when the session ends (tab closed, idle timeout, or
  **Disconnect**).
- **In the browser:** the page keeps it in memory only, not in localStorage.
- **Billing:** any cost is billed by the provider to the visitor's own account; the console shows
  tokens per session, and the estimated cost for OpenAI.
