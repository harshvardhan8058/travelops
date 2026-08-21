# 31. Team Actions — the three things only you can do

Everything else is either already built or owned by a stream. These three need a human with
access, and this document is precise enough that nobody has to ask a follow-up question.

Status summary lives in [`30-project-status.md`](30-project-status.md).

---

## Action 1 — Groq API key

### Why it is needed

Only for `LLM_MODE=live`, which is Phase 3 work targeting **Stage 3 (1–2 September)**.
Stage 2 does not need it: the deterministic path and `LLM_MODE=fixture` cover everything.

### Exactly what to do

1. Go to <https://console.groq.com/> and sign in.
2. Confirm **`llama-3.3-70b-versatile`** appears in the models list. If it has been renamed or
   retired, tell me the current model ID — that is a real change and I need to know.
3. Open **API Keys → Create API Key**. Name it `travelops-techcon`.
4. Copy the key. It is shown once.
5. Put it in `backend/.env` (create the file with `make env` if it does not exist):

   ```dotenv
   GROQ_API_KEY=gsk_your_key_here
   LLM_MODE=fixture
   ```

   Leave `LLM_MODE=fixture` until Stage 3. Switch to `live` only when Stream A is ready.

6. Open **Settings → Limits** and note the numbers shown for your account: requests per
   minute, requests per day, tokens per minute, tokens per day.

### What to send me

**The rate limit numbers only.** Plain text or a screenshot of the Limits page is fine.

**Never send the key** — not in chat, not in a commit, not in an issue, not in a screenshot.
`.env` is already in `.gitignore`, so a normal `git add` cannot capture it. Verify any time:

```bash
git check-ignore -v backend/.env    # should print the matching .gitignore rule
```

### How you know it worked

```bash
cd backend && uv run python -c "
from app.config import Settings, resolve_modes
m = resolve_modes(Settings(llm_mode='live'))
print('live mode accepted, resolved to:', m.llm.value)
"
```

Without a key this raises `ConfigurationError` naming `GROQ_API_KEY` — that refusal is the
fail-closed design working, not a bug.

### If it never arrives

`LLM_MODE=fixture` replays recorded responses and `off` uses the deterministic playbook. Both
complete a full recovery. The demo survives; we simply describe the reasoning agents as
specified rather than demonstrated.

---

## Action 2 — Run the stack once on the demo laptop

### Status: API confirmed running on Windows + Docker Desktop 29.x (WSL2), 21 August

`docker compose up --build -d` builds and starts the stack, and `/docs` serves all 12
endpoints. That closes the project's highest-risk unknown: until this point the stack had
never been executed anywhere, because the build sandbox blocks containers.

Still to confirm on the same machine:

- [ ] `docker compose ps` shows postgres and redis **healthy**
- [ ] `alembic upgrade head` prints `Running upgrade -> 0001_initial_schema` with no traceback
- [ ] <http://127.0.0.1:5173> renders the console: graphite background, network tiles, flight
      rows, timeline rail on the right
- [ ] Projector legibility from three metres
- [ ] A video plays with Wi-Fi disabled

### Why it matters

Do this on the **exact laptop that will present**, on the network you will present from. A
stack that runs on one developer's machine and not on the demo machine is the classic way a
checkpoint is lost, and the worst possible time to discover it is on the day.

### Exactly what to do — macOS or Linux

```bash
git clone https://github.com/harshvardhan8058/travelops.git
cd travelops

make doctor     # 1. checks toolchain and required files
make env        # 2. creates .env from .env.example
make up         # 3. builds and starts api, postgres, redis, web
make migrate    # 4. applies the schema
```

### Exactly what to do — Windows PowerShell

**`make` does not exist on Windows and `&&` is not a PowerShell separator.** Run the same four
steps directly. Verified working on Docker Desktop 29.x with the WSL2 backend.

```powershell
git clone https://github.com/harshvardhan8058/travelops.git
cd travelops          # the repo is a SUBFOLDER - compose lives here, not in the parent

docker --version      # 1. confirm Docker is installed and the daemon responds
docker info

Copy-Item .env.example .env        # 2. equivalent of `make env`

docker compose up --build -d       # 3. equivalent of `make up`
docker compose ps                  #    wait until postgres and redis show healthy

docker compose run --rm api alembic upgrade head   # 4. equivalent of `make migrate`
```

If `Copy-Item` reports `Cannot find path ...\.env.example` or compose reports
`no configuration file provided: not found`, you are one directory too high. `cd travelops`
and try again — that is the single most common mistake here.

PowerShell equivalents for the remaining targets:

| Makefile target | PowerShell |
| --- | --- |
| `make up` | `docker compose up --build -d` |
| `make down` | `docker compose down` |
| `make logs` | `docker compose logs -f api` |
| `make ps` | `docker compose ps` |
| `make migrate` | `docker compose run --rm api alembic upgrade head` |
| `make seed` | `docker compose run --rm api python -m app.cli seed` |
| `make demo` | `docker compose run --rm api python -m app.cli inject --scenario bengaluru_storm` |
| `make db-shell` | `docker compose exec postgres psql -U travelops -d travelops` |

`make doctor` has no direct equivalent; `docker --version` plus `docker info` covers the part
that matters. WSL2 is an alternative if you prefer the Make targets: `wsl --install`, then work
from `/mnt/c/...`.

### Then open both of these in the browser

- <http://127.0.0.1:8000/docs> — the API documentation
- <http://127.0.0.1:5173> — the operations console

### What success looks like

| Step | Expected |
| --- | --- |
| `make doctor` | Ends with `Ready. Next: make up && make migrate` and no ✗ lines |
| `make up` | Four containers running; `docker compose ps` shows `healthy` for postgres and redis |
| `make migrate` | `Running upgrade -> 0001_initial_schema` and no traceback |
| `/docs` | Swagger UI listing 12 endpoints |
| `:5173` | Dark graphite console, network tiles, flight rows, timeline rail on the right |

The console will show a degradation banner reading `LLM_MODE=off` and `WEATHER_MODE=fixture`.
**That is correct** — it is the system honestly reporting that it is running on fixtures.

### What to send me if anything fails

Copy the **exact text** of the error plus which of the four steps produced it. That is
genuinely all I need — please do not summarise it, because the specific message identifies the
cause.

### Likely failures and their fixes

| Symptom | Cause | Fix |
| --- | --- | --- |
| `docker: command not found` | Docker not installed | Install Docker Desktop, or ask IT for an approved runtime |
| `Cannot connect to the Docker daemon` | Docker not running | Start Docker Desktop and wait for it to report running |
| `port is already allocated` | 8000 or 5173 in use | Stop the other process, or change the host port in `docker-compose.yml` |
| Container exits immediately | Low memory | Give Docker at least 4 GB; 8 GB total recommended |
| `failed to solve: ... tls` | Corporate proxy or TLS inspection | Ask IT for the registry proxy settings for Docker |
| `make: command not found` | No `make` on Windows | Use WSL2, or run the commands inside the Makefile directly |

If Docker is blocked by policy, that is not something the team can work around alone — ask
Coforge IT or your project DevOps for an approved local runtime or a project VM, and tell me,
because it changes the delivery plan.

### Also worth doing while you are there

- Connect the projector and confirm the console is readable from three metres.
- Play a video with Wi-Fi disabled, to prove the offline backup path works.

---

## Action 3 — SME sign-off on the policy rules

### Why it is needed

To move `POLICY_MODE` from `charter` to `verified`. Without it we can still show **real, cited
figures** from the Ministry of Civil Aviation Passenger Charter behind a badge reading
*PENDING CAR VERIFICATION*. With it, we can state those figures are current law.

The system refuses `verified` mode by design until this exists. That refusal is deliberate.

### Who counts

An authorised **aviation, legal or compliance reviewer** — someone whose judgement the team can
point to. Ask, in order:

1. Your scheduled mentor.
2. The Arcolab or CIMS delivery / architecture lead, for a TTH airline-domain SME.
3. `TechCon.x@Coforge.com`, asking for the approved route for domain validation.

**A teammate reading the PDF and forming an opinion does not count.** The value of this step is
that an accountable person reviewed it.

### The message to forward

> We have encoded the Ministry of Civil Aviation Passenger Charter (February 2019) into a
> versioned rules pack for our TechCon prototype. Every rule cites its source and nothing is
> marked approved yet, so the system currently refuses to present any figure as current law.
>
> Could you review our interpretation? It is a clause-by-clause sheet of about 40 rules with
> 23 worked test cases, and eight specific questions where our reading is a judgement call
> rather than a direct reading. We estimate 60–90 minutes.
>
> The most important question: whether this 2019 charter is superseded for any encoded
> entitlement by the later revision of CAR Section 3, Series M, Part IV.

### What to give them

Two files from the repository:

- `policy_packs/in-moca-charter-2019/2019.02/rules.yaml` — the 40 encoded rules, each with its
  source clause reference and our paraphrase
- `policy_packs/in-moca-charter-2019/2019.02/review.yaml` — the sign-off sheet with the eight
  open questions

Optionally `test_cases.yaml`, which shows the worked outcomes in plain terms.

### What they return

`review.yaml` completed: reviewer name, role, date, and per rule marked **approved**,
**corrected** or **rejected**. Any correction needs the right position and its source.

The eight questions matter more than the rules, because each is a point where we made a
judgement call. Two are worth flagging to them explicitly:

- **RQ-1** — Is this charter superseded for any encoded entitlement by the later Part IV
  revision (reported as August 2024)?
- **RQ-3** — Our reading is that hotel accommodation requires more than 24 hours' notice
  *plus* either a 24-hour delay or a six-hour delay for a 20:00–03:00 departure. That appears to
  exclude a passenger delayed overnight at short notice, which does not feel like the drafting
  intent. Is our reading right?

### If it never arrives

We stay in `charter` mode and say so plainly: *"These figures come from an official Ministry of
Civil Aviation publication. We have not verified them against the current CAR revision, so the
engine blocks verified mode."*

That is a defensible position. Claiming verification we do not have is not.

---

## Quick reference

| # | Action | Deadline | Blocks | Fallback |
| --- | --- | --- | --- | --- |
| 1 | Groq key in `backend/.env`; send limits only | Before Stage 3 | Live reasoning | `fixture` and `off` both work |
| 2 | Run the stack on the demo laptop — API ✅ 21 Aug; console + migration pending | **Now** | Confidence the stack runs | **None** |
| 3 | SME completes `review.yaml` | Before Stage 3 | `POLICY_MODE=verified` | `charter` mode, dated badge |

Nothing else is required from the team. The full not-needed list — paid APIs, real passenger
data, SMS gateways, Kubernetes, agent frameworks — is in
[`30-project-status.md`](30-project-status.md) section 1.
