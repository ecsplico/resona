# CLI Setup & Onboarding

This guide walks you through installing the `resona` CLI and connecting it to a
Resona server — whether that server is on the same machine, your local
network, or a remote machine over the internet.

!!! info "Server not set up yet?"
    This guide assumes a Resona server is already running somewhere.
    See [Server Setup](getting-started.md) to stand one up first.

---

## 1. Install resona CLI

The `resona` CLI lives in the monorepo. You need to clone it and install the workspace:

**Prerequisites:** [uv](https://docs.astral.sh/uv/), Python 3.12+, `ffmpeg` in PATH.

```bash
git clone <repo-url>
cd resona

uv sync --all-packages --no-build-isolation-package openai-whisper
```

Verify the install:

```bash
resona --help
```

You should see the full command list (`watch`, `transcribe`, `rec`, `live`, `ui`,
`engines`, `replacements`, `prompts`).

---

## 2. Connect to a server

`resona` stores engine addresses in `~/.resona/config.json`.
Engines are tried in priority order; the first reachable one is used.

Pick the scenario that matches your setup:

=== "Same machine"

    The server is running locally (e.g. `docker compose -f docker-compose.resona.yml up` in the repo, or
    `uv run resona-api`):

    ```bash
    resona engines add local http://localhost:7000
    ```

    If the server needs an API key (i.e. `RESONA_API_KEY` was set):

    ```bash
    resona engines add local http://localhost:7000 --key YOUR_API_KEY
    ```

    If you want `resona` to start the server automatically when it's not running:

    ```bash
    resona engines add local http://localhost:7000 \
      --compose-dir ~/resona
    ```

    With `--compose-dir` set, `resona` will run `docker compose up -d` and wait
    up to 120 seconds whenever the engine is unreachable.

=== "LAN (same network)"

    The server is on another machine on your local network. Find its IP address
    (e.g. `192.168.1.50`) and use port `7000`:

    ```bash
    resona engines add lan http://192.168.1.50:7000
    ```

    With an API key:

    ```bash
    resona engines add lan http://192.168.1.50:7000 --key YOUR_API_KEY
    ```

    No auto-start is available for direct LAN engines.

=== "Remote (SSH tunnel)"

    The server is on a machine accessible only via SSH (home server, VPS, etc.).
    `resona` will open a local port-forward tunnel automatically.

    **Prerequisites:**

    - SSH key-based auth must work: `ssh user@myserver.com` must not prompt for a password
    - `resona-api` must be running on the remote machine (default port `7000`)

    **Setup:**

    ```bash
    resona engines add remote http://localhost:7000 \
      --ssh user@myserver.com
    ```

    This tells `resona`:

    - Connect locally at `http://localhost:7000`
    - Before connecting, open: `ssh -N -L 7000:localhost:7000 user@myserver.com`

    Common variations:

    ```bash
    # Non-standard SSH port
    resona engines add remote http://localhost:7000 \
      --ssh user@myserver.com:2222

    # API key on the remote server
    resona engines add remote http://localhost:7000 \
      --ssh user@myserver.com \
      --key YOUR_API_KEY

    # Avoid port conflict with a local service on :7000
    resona engines add remote http://localhost:17000 \
      --ssh user@myserver.com \
      --ssh-remote-port 7000
    ```

    The tunnel is opened on demand and killed automatically when `resona` exits.

    !!! warning "SSH host key"
        The first connection will accept the server's host key automatically
        (`StrictHostKeyChecking=accept-new`). If the host key later changes
        (e.g. server reinstall), the connection will be refused — update
        `~/.ssh/known_hosts` manually.

---

## 3. Verify the connection

```bash
resona engines list
```

Example output:

```
  ✓  local                http://localhost:7000  [compose: ~/resona]
  ✓  remote               http://localhost:7000  [ssh: user@myserver.com]
```

A `✓` means the engine responded to `GET /health`. A `✗` means it's
unreachable (but may still auto-start when needed).

Test a specific engine explicitly:

```bash
resona engines test remote
```

---

## 4. First transcription

Once an engine is reachable, transcribe a file:

```bash
resona transcribe ./recordings/ --output-dir ./transcripts/
```

This submits every audio file in `./recordings/`, waits for all jobs to
complete, and saves the transcripts to `./transcripts/`. You can also pass a
single file (`resona transcribe recording.mp3`) or a quoted glob
(`resona transcribe "recordings/*.mp3"`).

Or watch a folder and auto-submit anything dropped in:

```bash
resona watch ./inbox/
```

---

## 5. Text replacements

Replacements are regex patterns applied to every transcript after transcription.
The server stores them; `resona` manages them:

```bash
# See what's already configured
resona replacements list

# Add spoken punctuation → symbols
resona replacements add "Komma" ","
resona replacements add "Punkt" "."
resona replacements add "Ausrufezeichen" "!"

# Remove an entry
resona replacements delete 3
```

Patterns are matched case-insensitively against the raw transcript, in
creation order. Replacements are applied by `resona-api` (via `resona-postprocess`)
after the engine returns the raw text — the engine itself does not apply them.

---

## 6. Initial prompts (optional)

An initial prompt biases Whisper towards specific vocabulary. Useful for
domain terms the model struggles with:

```bash
resona prompts add "Befund, Diagnose, Therapie, Anamnese"
resona prompts activate 1
```

Only one prompt is active at a time. Deactivate when not needed:

```bash
resona prompts deactivate 1
```

---

## 7. TUI tools

```bash
resona rec    # Record audio to a WAV file (no transcription)
resona live   # Live transcription — streams mic audio to the engine in real time
resona ui     # Record, then auto-submit for transcription and view the result
```

`resona ui` is the most useful for dictation: record, press **Save**, and the
transcript appears in a new tab once the job completes.

---

## Multiple engines

You can register several engines. `resona` tries them in order and uses the
first reachable one — useful if you want a fast LAN server with an SSH
fallback:

```bash
resona engines add lan    http://192.168.1.50:7000
resona engines add remote http://localhost:7000 --ssh user@myserver.com
```

Reorder by removing and re-adding. Remove with:

```bash
resona engines remove remote
```

See [Engines & SSH](configuration/engines.md) for the full resolution logic.
