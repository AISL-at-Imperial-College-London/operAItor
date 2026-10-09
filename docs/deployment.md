# Deployment

The console is a Python server (plant simulation, agents, WebSocket) plus a static React build,
shipped as one Docker image. It needs a host that runs a container permanently. Static hosts such
as GitHub Pages or Vercel cannot run the simulation.

## Hugging Face Spaces (free, recommended for demos)

A Docker Space on the free **CPU basic** hardware (2 vCPU, 16 GB) runs the image as is and gives a
public `https://<owner>-<space>.hf.space` URL.

One-time setup:
1. Create the Space at https://huggingface.co/new-space with SDK **Docker**, a blank template and
   hardware **CPU basic**.
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
docker build -t acetone-plant .
docker run -p 7860:7860 acetone-plant
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
| `PLANT_ALLOWED_MODELS` | `gpt-5-nano,gpt-5-mini` | Models a visitor may choose |
| `PLANT_LLM_MODEL` | `gpt-5-nano` | Default model |
| `PLANT_AGENT` | (llm) | Default agent configuration for new sessions |
| `PLANT_CAMPAIGN_DIR` | (empty) | Where console campaign reports are written; empty = not written |
| `PORT` | `7860` | Listening port |

For a private deployment with your own key, set `PLANT_HOSTED=0` and `OPENAI_API_KEY`. Every
session then uses the server key. Protect such a deployment yourself (e.g. a password on the
reverse proxy), because anyone who reaches it spends your key.

## Visitor keys: what happens to them

- **Sending:** the key goes from the browser to the server over the WebSocket. Use HTTPS; Spaces
  and Render provide it.
- **Checking:** the server checks the key with a free `GET /v1/models` call.
- **Use:** it then builds that session's agents with the key. The key is held in process memory
  only and is never written to disk or logs.
- **Isolation:** it is never sent back to the browser, and other sessions cannot use it.
- **End of session:** it is dropped when the session ends (tab closed, idle timeout, or "Remove my
  key").
- **In the browser:** the page keeps it in memory only, not in localStorage. A reload asks for it
  again.
- **Billing:** visitors pay for their own calls; the console shows tokens and estimated cost per
  session.
