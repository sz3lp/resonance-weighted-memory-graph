# Repository Contents

This file contains the contents of repository files under this tree, organized by path.
The snapshot excludes `repository_contents.md` itself to avoid recursive duplication.

## `create_reddit_accounts.py`

```python
import asyncio
import json
import os
import random
import re
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import requests
from playwright.async_api import async_playwright


# --------------------------- Utility helpers ---------------------------

def load_agents(manifest_path: str) -> List[Dict[str, str]]:
    """Load agent definitions from JSON manifest."""
    with open(manifest_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "agents" in data:
        return data["agents"]
    return data  # assume already a list


def load_proxies(proxy_path: str) -> List[str]:
    """Read proxy list from a text file."""
    with open(proxy_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def parse_proxy(proxy: str) -> Dict[str, str]:
    """Convert proxy string http://user:pass@host:port to Playwright config."""
    pattern = re.compile(
        r"^(?P<scheme>https?)://(?:(?P<user>[^:]+):(?P<password>[^@]+)@)?(?P<host>[^:]+):(?P<port>\d+)$"
    )
    match = pattern.match(proxy)
    if not match:
        raise ValueError(f"Invalid proxy format: {proxy}")
    groups = match.groupdict()
    config = {"server": f"{groups['scheme']}://{groups['host']}:{groups['port']}"}
    if groups.get("user"):
        config["username"] = groups["user"]
        config["password"] = groups["password"]
    return config


def generate_username(name: str) -> str:
    base = re.sub(r"\W+", "", name.lower())[:10]
    return f"{base}{random.randint(1000, 9999)}"


def generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + string.punctuation
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def human_type(locator, text: str) -> None:
    for ch in text:
        await locator.type(ch, delay=random.uniform(50, 150))
        await asyncio.sleep(random.uniform(0.05, 0.15))


async def solve_captcha(page, proxy: str) -> None:
    """Solve hCaptcha via 2Captcha service.

    This function expects the environment variable ``TWO_CAPTCHA_API_KEY`` to be
    set.  The token is injected into the page once retrieved.
    """

    api_key = os.getenv("TWO_CAPTCHA_API_KEY")
    if not api_key:
        raise RuntimeError("TWO_CAPTCHA_API_KEY environment variable not set")

    iframe = page.locator("iframe[src*='hcaptcha.com']")
    if not await iframe.count():
        return  # no captcha present

    src = await iframe.first.get_attribute("src")
    match = re.search(r"sitekey=([^&]+)", src or "")
    if not match:
        raise RuntimeError("Unable to locate hCaptcha sitekey")

    sitekey = match.group(1)
    payload = {
        "key": api_key,
        "method": "hcaptcha",
        "sitekey": sitekey,
        "pageurl": page.url,
        "json": 1,
    }

    proxies = {"http": proxy, "https": proxy}
    resp = await asyncio.to_thread(
        requests.post, "http://2captcha.com/in.php", data=payload, proxies=proxies, timeout=30
    )
    resp_data = resp.json()
    if resp_data.get("status") != 1:
        raise RuntimeError(f"2Captcha failed to accept task: {resp_data.get('request')}")

    request_id = resp_data["request"]
    result_params = {"key": api_key, "action": "get", "id": request_id, "json": 1}

    token: str | None = None
    for _ in range(24):  # wait up to ~2 minutes
        await asyncio.sleep(5)
        result = await asyncio.to_thread(
            requests.get, "http://2captcha.com/res.php", params=result_params, proxies=proxies, timeout=30
        )
        result_data = result.json()
        if result_data.get("status") == 1:
            token = result_data["request"]
            break

    if not token:
        raise RuntimeError("CAPTCHA solving timed out")

    await page.evaluate(
        "document.querySelector('textarea[name="h-captcha-response"]').value = arguments[0];",
        token,
    )


def save_credentials(agent: Dict[str, str], username: str, password: str, proxy: str) -> None:
    payload = {
        "reddit_username": username,
        "reddit_password": password,
        "reddit_email": agent["email"],
        "reddit_created_at": datetime.now(timezone.utc).isoformat(),
        "proxy": proxy,
    }
    secrets_path = Path(agent["secrets_path"])
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    with open(secrets_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"Saved credentials for {agent['name']} -> {secrets_path}")


# --------------------------- Core workflow ---------------------------

async def create_reddit_account(agent: Dict[str, str], proxy: str) -> None:
    username = generate_username(agent["name"])
    password = generate_password()

    async with async_playwright() as p:
        proxy_conf = parse_proxy(proxy)
        browser = await p.chromium.launch(headless=True, proxy=proxy_conf)
        context = await browser.new_context()
        page = await context.new_page()

        # Navigate to signup page
        await page.goto("https://www.reddit.com/register", timeout=120000)

        # Email
        email_input = page.locator("input[name='email']")
        await email_input.click()
        await human_type(email_input, agent["email"])
        await page.locator("button[type='submit']").click()
        await asyncio.sleep(random.uniform(1, 3))

        # Username and password
        user_input = page.locator("input[name='username']")
        pass_input = page.locator("input[name='password']")
        await human_type(user_input, username)
        await human_type(pass_input, password)

        # CAPTCHA
        await solve_captcha(page, proxy)

        # Submit form
        await page.locator("button[type='submit']").click()
        await asyncio.sleep(random.uniform(5, 8))

        await browser.close()

    save_credentials(agent, username, password, proxy)


async def main() -> None:
    agents = load_agents("input/agents/persona_manifest.json")
    proxies = load_proxies("input/proxies.txt")
    if len(proxies) < len(agents):
        raise RuntimeError("Not enough proxies for the number of agents")

    proxy_map: Dict[str, str] = {}

    for agent, proxy in zip(agents, proxies):
        agent_id = Path(agent["secrets_path"]).stem
        proxy_map[agent_id] = proxy
        try:
            print(f"Creating account for {agent['name']} using proxy {proxy}")
            await create_reddit_account(agent, proxy)
        except Exception as exc:
            print(f"Failed to create account for {agent['name']}: {exc}")

    proxies_path = Path("rwmg/secrets/proxies_map.json")
    proxies_path.parent.mkdir(parents=True, exist_ok=True)
    with proxies_path.open("w", encoding="utf-8") as fh:
        json.dump(proxy_map, fh, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
```

## `input/agents/persona_manifest.json`

```json
[
  {
    "name": "Cassian Rhys",
    "email": "cassian.rhys@ospreyexterior.com",
    "secrets_path": "rwmg/secrets/agent_keys/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f.json"
  }
]
```

## `input/proxies.txt`

```text
http://user:pass@proxy1.example.com:8080
http://user:pass@proxy2.example.com:8080
```

## `README.md`

```markdown
# RWMG Simulation

This repository provides a minimal agent-based simulation for the RWMG project. It can create agents, let them post to platforms, collect feedback and update their memories.

## Key modules
- **`sim_runner`** – orchestrates simulation epochs and bootstraps agents. `run_epoch` iterates through active agents, executes daily rituals and logs metrics.
- **`lifecycle_manager`** – drives each agent's daily routine: rank memories, build prompts, call the Gemini API and record new events.
- **`feedback`** – logs posts and normalises engagement data into a canonical form for weighting memories.
- **`utils`** – shared helpers for API calls, memory ranking and timestamp handling.

## Basic usage
1. **Configure agents** by adjusting YAML files under `config/` (archetypes, platform profiles, etc.) and supplying any API keys under `secrets/`.
2. **Create agents and manifest:**
   ```python
   from rwmg.sim_runner.sim_start import create_agents, populate_manifest
   agents = create_agents(1, {}, {"email_domain": "example.com"})
   populate_manifest(list(agents.keys()))
   ```
3. **Run a sample epoch:**
   ```python
   from rwmg.sim_runner.epoch_runner import run_epoch
   import json, pathlib

   manifest_path = pathlib.Path("agents/persona_manifest.json")
   agent_manifest = json.loads(manifest_path.read_text())
   run_epoch(agent_manifest, epoch_length=1)
   ```
   The example runs one simulated day for each registered agent.

## Reddit account creation (Grandpa's guide)
The repository also includes a small script to register Reddit accounts for your agents. Here is a slow and steady walk-through:

1. **Get Python ready**  
   Make sure Python 3.10 or newer is installed. On Windows you can grab it from [python.org](https://www.python.org/downloads/). When the installer asks, tick the box that says “Add Python to PATH”.

2. **Open your command window**  
   - On Windows: press the Start button, type “cmd”, and hit Enter.  
   - On macOS: open “Terminal” from Applications → Utilities.  
   - On Linux: open whichever terminal program you like.

3. **Install the tools**  
   Copy and paste the following line into the terminal and press Enter:
   ```bash
   pip install playwright requests python-dotenv
   ```
   When it finishes, run one more command so Playwright can download a browser:
   ```bash
   playwright install
   ```

4. **Tell the script about your agents**  
   Open `input/agents/persona_manifest.json` in a text editor. Each agent should look like this:
   ```json
   {
     "name": "Cassian Rhys",
     "email": "cassian.rhys@ospreyexterior.com",
     "secrets_path": "rwmg/secrets/agent_keys/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f.json"
   }
   ```
   Add more entries to the list if you have more agents. Save the file when you're done.

5. **Set up the proxy list**  
   Open `input/proxies.txt`. Each line should have one proxy in the form:
   ```
   http://user:pass@proxy1.example.com:8080
   ```
   Put one proxy per line. The script will use the first proxy for the first agent, the second proxy for the second agent, and so on.

6. **Run the account creator**  
   Back in the terminal, run:
   ```bash
   python create_reddit_accounts.py
   ```
   The script opens a hidden browser in the background. If Reddit shows a CAPTCHA, the program pauses for two minutes so you can solve it manually.

7. **Look for the results**  
   When an account is created, the script writes a small JSON file containing the username, password, and email. You told the script where to put this file using `secrets_path` above. Check that location to see the saved credentials.

Take your time with each step. If something doesn't look right, read the message on the screen carefully and try again. Nothing is rushed here.

## Tests and programmatic checks
Run the project’s tests once they become available:
```bash
pytest
```
Add any linters or type checks as the project grows and run them alongside the tests.
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/agent_state.json`

```json
{
  "agent_id": "string",
  "emotional_vector": {
    "valence": "float",
    "arousal": "float",
    "dominance": "float"
  },
  "emotional_state": {
    "joy": "float",
    "anger": "float",
    "grief": "float",
    "contempt": "float",
    "affinity": "float",
    "stress": "float"
  },
  "current_tone": "string",
  "dominant_style": "string",
  "is_in_ritual": "boolean",
  "ritual_stage": "string",
  "last_post_timestamp": "ISO 8601 string",
  "posting_likelihood": "float",
  "status": "active | dormant | recalibrating",
  "archetype_blend": {
    "Lover": "float",
    "Warrior": "float",
    "King": "float",
    "Magician": "float"
  },
  "trait_vector": {
    "openness": "float",
    "conscientiousness": "float",
    "extraversion": "float",
    "agreeableness": "float",
    "neuroticism": "float"
  }
}
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/canonical_events.json`

```json
[
  {
    "event_id": "string",
    "age": "integer",
    "timestamp": "ISO 8601 string",
    "type": "string",
    "description": "string",
    "trait_shift": [
      {
        "trait": "string",
        "value": "float"
      }
    ]
  }
]
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/connections.json`

```json
{
  "friends": [
    {
      "agent_id": "string",
      "relationship_type": "string",
      "affinity_score": "float"
    }
  ],
  "mentors": [
    {
      "agent_id": "string",
      "affinity_score": "float"
    }
  ]
}
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/divergence_report.json`

```json
{
  "last_checked": "ISO 8601 string",
  "is_diverged": "boolean",
  "divergence_score": "float",
  "detected_discrepancies": [
    {
      "type": "string",
      "description": "string",
      "source_of_truth": "string"
    }
  ]
}
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/interest_graph.json`

```json
{}
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/memory_cache_top5.json`

```json
[
  {
    "event_id": "string",
    "content": "string",
    "weight": "float"
  }
]
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/memory_graph.gexf`

```xml
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/memory_index.csv`

```csv
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/memory_log.json`

```json
[
  {
    "event_id": "string",
    "timestamp": "ISO 8601 string",
    "content": "string",
    "platform": "string",
    "resonance_score": "float",
    "injected_memories": "list of strings",
    "feedback_data_ref": "string"
  }
]
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/memory_tags.json`

```json
{
  "event_id_001": ["attachment", "abandonment", "childhood", "sadness", "lover_shadow"],
  "event_id_002": ["peer_rejection", "humiliation", "adolescence", "anger", "vulnerability"]
}
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/narrative_summary.json`

```json
{
  "last_updated": "ISO 8601 string",
  "dominant_theme": "string",
  "current_arc": "Collapse Loop | Breakthrough | Plateau",
  "major_breakpoints": [
    {
      "event_id": "string",
      "event_type": "string",
      "description": "string"
    }
  ]
}
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/persona_meta.yaml`

```yaml
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/profile.json`

```json
{
  "agent_id": "string",
  "name": "string",
  "birthday": "ISO 8601 string",
  "archetype_core": "King | Lover | Warrior | Magician",
  "email": "string",
  "secrets_path": "string"
}
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/self_reflection.json`

```json
{
  "last_run": "ISO 8601 string",
  "perceived_persona": "string",
  "internal_dissonance_report": [
    {
      "source_a": "string",
      "source_b": "string",
      "dissonance_description": "string"
    }
  ],
  "emergent_tone_report": {
    "current_dominant_tone": "string",
    "recent_style_changes": "string"
  }
}
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/suppresion_log.json`

```json
[
  {
    "event_id": "string",
    "content": "string",
    "weight": "float"
  }
]
```

## `rwmg/agents/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f/suppression_log.json`

```json
```

## `rwmg/agents/persona_manifest.json`

```json
{
  "UUIDv4": {
    "name": "string",
    "archetype_core": "string",
    "email": "string",
    "status": "active | dormant | retired",
    "created_at": "ISO 8601 string"
  }
}
```

## `rwmg/config/agent_behavior_profiles.yaml`

```yaml
sporadic:
  post_frequency: "random"
  cooldown_period_hours: [1, 24]
  activity_windows: "random"

ritualistic:
  post_frequency: "daily"
  cooldown_period_hours: [23, 25]
  activity_windows: "fixed_morning"

hyperactive:
  post_frequency: "3x/day"
  cooldown_period_hours: [1, 5]
  activity_windows: "all_day"

quiet_observer:
  post_frequency: "1x/week"
  cooldown_period_hours: [168, 172]
  activity_windows: "sporadic"

night_owl:
  post_frequency: "daily"
  cooldown_period_hours: [20, 28]
  activity_windows: "night"

morning_person:
  post_frequency: "daily"
  cooldown_period_hours: [22, 26]
  activity_windows: "morning"

afternoon_poster:
  post_frequency: "daily"
  cooldown_period_hours: [22, 26]
  activity_windows: "afternoon"

weekend_warrior:
  post_frequency: "1x/week"
  cooldown_period_hours: [144, 192]
  activity_windows: "all_day"

lunch_breaker:
  post_frequency: "daily"
  cooldown_period_hours: [20, 28]
  activity_windows: "midday"

chaotic:
  post_frequency: "random"
  cooldown_period_hours: [2, 12]
  activity_windows: "random"

```

## `rwmg/config/archetype_rules.yaml`

```yaml
King:
  event_probabilities:
    early_trauma: 0.1 # Less likely to have early trauma, more likely to have tests of power
    leadership_trial: 0.8
    betrayal_of_trust: 0.6
  keywords: ["sovereignty", "order", "duty", "responsibility", "legacy"]
  event_type_multiplier: 1.2 # King events have a higher narrative weight
Lover:
  event_probabilities:
    early_trauma: 0.6 # More likely to have foundational emotional trauma
    relationship_breakup: 0.9
    abandonment: 0.7
  keywords: ["intimacy", "betrayal", "longing", "yearning", "vulnerability"]
  event_type_multiplier: 1.5 # Lover events, when they hit, have a high emotional impact
Warrior:
  event_probabilities:
    early_trauma: 0.3
    physical_conflict: 0.8
    test_of_discipline: 0.9
  keywords: ["discipline", "struggle", "victory", "force", "courage"]
  event_type_multiplier: 1.1
Magician:
  event_probabilities:
    early_trauma: 0.4
    intellectual_betrayal: 0.7
    system_collapse: 0.8
  keywords: ["insight", "system", "pattern", "knowledge", "transformation"]
  event_type_multiplier: 1.3
```

## `rwmg/config/persona_meta.yaml`

```yaml
# Immutable, authored traits
tone_preference: "sarcastic" # e.g., "sarcastic", "vulnerable", "stoic"
writing_style: "short, punchy" # e.g., "short, punchy", "long-form, explanatory"
collapse_state_label: "Wounded" # e.g., "Tyrant", "Wounded", "Overmind", "Fragmented"
foundational_backstory_summary: "The agent is a former tradesman who lost his sense of purpose after a physical injury."
```

## `rwmg/config/platform_profiles.yaml`

```yaml
twitter:
  max_tokens: 280
  style: "witty, brief, direct"
  decay_multiplier: 1.1 # Faster decay on ephemeral platforms
  like_weight: 0.1
  share_weight: 0.8 # Shares are highly valued as they signal belief and momentum
  comment_weight: 0.5
reddit:
  max_tokens: 3000
  style: "explanatory, argumentative, in-depth"
  decay_multiplier: 0.9 # Slower decay on long-form platforms
  like_weight: 0.3 # Upvotes are a baseline signal
  share_weight: 0.4
  comment_weight: 0.9 # Comments signal deep engagement and validation
```

## `rwmg/config/settings.yaml`

```yaml
# Global System Settings for the RWMG Engine

# API Keys and Credentials
# Note: These should ideally be loaded from environment variables or a secure vault
api_keys:
  gemini_api_key: "YOUR_GEMINI_API_KEY_HERE"
  twitter_api_key: "YOUR_TWITTER_API_KEY_HERE"
  reddit_api_key: "YOUR_REDDIT_API_KEY_HERE"
  discord_bot_token: "YOUR_DISCORD_BOT_TOKEN_HERE"

# Core System Toggles
feature_toggles:
  enable_multimodal_mirror: true # Controls the activation of the multimodal_mirror.py module
  enable_event_bus: true         # Enables or disables the asynchronous event bus system
  enable_explainability_mode: true # Toggles detailed logging for the explainability/ module

# Operational Parameters
operational_parameters:
  log_level: "INFO"              # Logging verbosity: DEBUG, INFO, WARNING, ERROR
  data_retention_days: 90        # Number of days to retain logs and temporary data
  agent_creation_batch_size: 5   # Number of agents to create at once during sim_start
```

## `rwmg/config/sim_config.yaml`

```yaml
num_agents: 50
archetype_distribution:
  lover: 0.4
  king: 0.3
  warrior: 0.2
  magician: 0.1
platforms: ["twitter", "reddit"]
epoch_days: 30
post_schedule: "random" # Can be "daily", "3x/week", or "random"
resonance_collection_interval: "2h" # How often to scrape for new feedback
evolution_check_interval: "weekly" # How often to run the evolution protocol
```

## `rwmg/explainability/memory_retrospective.py`

```python
"""Tools for visualizing an agent's memory weight over time."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import List

from ..utils.math_functions import exponential_decay


def _load_json(path: str):
    """Safely load JSON returning ``None`` when the file does not exist."""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def visualize_memory_history(agent_uuid: str) -> None:
    """Generate a timeline plot for the highest weighted memory.

    The function finds the memory with the greatest current weight from
    ``memory_cache_top5.json`` and visualises how its influence decays over
    time.  If ``matplotlib`` is available a PNG is written beside the agent's
    files; otherwise a JSON representation of the timeline is printed to
    stdout.
    """

    base_dir = os.path.join("rwmg", "agents", agent_uuid)
    cache_path = os.path.join(base_dir, "memory_cache_top5.json")
    log_path = os.path.join(base_dir, "memory_log.json")

    cache = _load_json(cache_path) or []
    if not cache:
        return

    top_memory = cache[0]
    event_id = top_memory.get("event_id")
    initial_weight = float(top_memory.get("weight", 0.0))

    log = _load_json(log_path) or []
    event = next((e for e in log if e.get("event_id") == event_id), None)
    if not event:
        return

    start_time = datetime.fromisoformat(event.get("timestamp"))
    now = datetime.utcnow()
    days = max((now - start_time).days, 1)

    timeline_dates: List[datetime] = []
    timeline_weights: List[float] = []

    for day in range(days + 1):
        timeline_dates.append(start_time + timedelta(days=day))
        timeline_weights.append(
            exponential_decay(initial_weight, day, half_life=30)
        )

    try:
        import matplotlib.pyplot as plt  # type: ignore

        plt.figure(figsize=(6, 3))
        plt.plot(timeline_dates, timeline_weights, marker="o")
        plt.title(f"Memory weight history: {event_id}")
        plt.xlabel("Date")
        plt.ylabel("Weight")
        plt.tight_layout()
        out_path = os.path.join(base_dir, f"{event_id}_history.png")
        plt.savefig(out_path)
        plt.close()
    except Exception:
        # Fallback textual representation
        printable = {
            d.isoformat(): w for d, w in zip(timeline_dates, timeline_weights)
        }
        print(json.dumps({"event_id": event_id, "timeline": printable}, indent=2))

```

## `rwmg/explainability/post_traceback.py`

```python
"""Tools for explaining how a post came to be.

This module exposes :func:`trace_post_to_memories` which inspects the agents'
data directories to discover which memories were injected into the prompt that
generated a particular post.  The function is intentionally lightweight and
file‑system based; no external databases are involved in this project
blueprint.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List


# explainability/post_traceback.py
def trace_post_to_memories(event_id: str) -> Dict:
    """Trace a post back to the memories that influenced its generation.

    Parameters
    ----------
    event_id:
        Identifier of the post to trace.

    Returns
    -------
    dict
        A dictionary containing the ``agent_id`` of the posting agent, the
        ``post_event`` entry from its ``memory_log.json`` and a list of
        ``influencing_memories`` with basic details for each referenced memory.
        An empty dictionary is returned if the event cannot be located.
    """

    agents_root = Path(__file__).resolve().parents[1] / "agents"

    for agent_dir in agents_root.iterdir():
        if not agent_dir.is_dir():
            continue

        log_path = agent_dir / "memory_log.json"
        if not log_path.exists():
            continue

        try:
            with log_path.open("r", encoding="utf-8") as fh:
                log_entries: List[Dict] = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        for entry in log_entries:
            if entry.get("event_id") != event_id:
                continue

            injected_ids = entry.get("injected_memories", []) or []

            # Build a lookup table for memory entries from both the log and the
            # agent's canonical events.
            memory_lookup: Dict[str, Dict] = {
                m.get("event_id"): m for m in log_entries if m.get("event_id")
            }

            canon_path = agent_dir / "canonical_events.json"
            if canon_path.exists():
                try:
                    with canon_path.open("r", encoding="utf-8") as fh:
                        canon_entries: List[Dict] = json.load(fh)
                    memory_lookup.update(
                        {c.get("event_id"): c for c in canon_entries if c.get("event_id")}
                    )
                except (json.JSONDecodeError, OSError):
                    pass

            influencing = []
            for mem_id in injected_ids:
                mem = memory_lookup.get(mem_id)
                if not mem:
                    continue
                influencing.append(
                    {
                        "event_id": mem.get("event_id"),
                        "content": mem.get("content") or mem.get("description"),
                        "resonance_score": mem.get("resonance_score"),
                    }
                )

            return {
                "agent_id": agent_dir.name,
                "post_event": entry,
                "influencing_memories": influencing,
            }

    return {}

```

## `rwmg/explainability/trait_influence.py`

```python
"""Analyse how feedback altered an agent's internal state.

The project blueprint describes a feedback loop in which every post can
influence an agent's emotions and long‑term personality traits.  This module
provides a light‑weight, file based implementation of that analysis.  Given a
``event_id`` corresponding to a post, :func:`analyze_trait_shift` locates the
originating agent, loads the associated feedback data and simulates how the
post would have nudged the agent's emotional and trait vectors.  The function
is intentionally conservative – it never mutates files on disk – and simply
returns a report of the calculated deltas.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Return ``value`` constrained to the inclusive ``[low, high]`` range."""

    return max(low, min(high, value))


# explainability/trait_influence.py
def analyze_trait_shift(event_id: str) -> Dict:
    """Analyse the impact of a post on an agent's emotions and traits.

    Parameters
    ----------
    event_id:
        Identifier of the post to analyse.

    Returns
    -------
    dict
        Contains the ``agent_id`` and dictionaries describing the shift in the
        agent's ``emotional_state`` and ``trait_vector``.  Empty if the event
        cannot be located.
    """

    agents_root = Path(__file__).resolve().parents[1] / "agents"

    for agent_dir in agents_root.iterdir():
        if not agent_dir.is_dir():
            continue

        log_path = agent_dir / "memory_log.json"
        state_path = agent_dir / "agent_state.json"

        if not log_path.exists() or not state_path.exists():
            continue

        try:
            with log_path.open("r", encoding="utf-8") as fh:
                log_entries = json.load(fh)
            with state_path.open("r", encoding="utf-8") as fh:
                agent_state = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        for entry in log_entries:
            if entry.get("event_id") != event_id:
                continue

            feedback_data: Dict = {}
            feedback_ref = entry.get("feedback_data_ref")
            if isinstance(feedback_ref, str) and feedback_ref:
                candidate_paths = [
                    agent_dir / feedback_ref,
                    Path(feedback_ref),
                    agents_root.parent / feedback_ref,
                ]
                for path in candidate_paths:
                    if path.exists():
                        try:
                            with path.open("r", encoding="utf-8") as fh:
                                feedback_data = json.load(fh)
                        except (json.JSONDecodeError, OSError):
                            pass
                        break

            resonance = float(entry.get("resonance_score", 0.0))
            sentiment = float(feedback_data.get("average_sentiment_score", 0.0))
            human_ratio = float(feedback_data.get("human_comment_ratio", 0.0))

            # --- Emotional shift -------------------------------------------
            emo_before = agent_state.get("emotional_state", {})
            emo_after = {}
            emo_delta = {}
            for key, value in emo_before.items():
                val = float(value)
                new_val = _clamp(val + sentiment * resonance * 0.1)
                emo_after[key] = new_val
                emo_delta[key] = new_val - val

            # --- Trait shift -----------------------------------------------
            trait_before = agent_state.get("trait_vector", {})
            trait_after = {}
            trait_delta = {}
            for key, value in trait_before.items():
                val = float(value)
                new_val = _clamp(val + sentiment * human_ratio * resonance * 0.05)
                trait_after[key] = new_val
                trait_delta[key] = new_val - val

            return {
                "agent_id": agent_dir.name,
                "event": entry,
                "emotional_shift": emo_delta,
                "updated_emotional_state": emo_after,
                "trait_shift": trait_delta,
                "updated_trait_vector": trait_after,
            }

    return {}


```

## `rwmg/feedback/feedback_injector.py`

```python
"""Utilities for injecting social feedback into an agent's memory log.

This module closes the feedback loop by taking engagement data collected for a
post and persisting it as a memory entry.  The stored memory can later be used
by other components (e.g. context injectors or ranking utilities) to influence
future behaviour of the agent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from rwmg.utils.tagger import extract_memory_tags
from rwmg.utils.timestamp_utils import get_current_iso_time


# feedback/feedback_injector.py

def inject_feedback_as_memory(agent_id: str, post_id: str, feedback_data: Dict) -> None:
    """Append a feedback summary as a memory entry for ``agent_id``.

    Parameters
    ----------
    agent_id:
        Identifier of the agent whose memory log should be updated.
    post_id:
        Event identifier of the original post.
    feedback_data:
        Dictionary containing normalised post feedback.  Expected keys include
        ``resonance_score``, ``engagement`` (with ``upvotes``, ``comments``,
        ``top_comment`` and ``sentiment``), ``post_text``, ``timestamp`` and
        ``community``.
    """

    resonance = float(feedback_data.get("resonance_score", 0.0))
    engagement = feedback_data.get("engagement", {}) or {}
    upvotes = int(engagement.get("upvotes", 0))
    community = str(feedback_data.get("community", ""))
    top_comment = str(engagement.get("top_comment", ""))

    summary = f"Post received {upvotes} upvotes"
    if community:
        summary += f" in {community}"
    summary += "."
    if top_comment:
        summary += f" Top comment: '{top_comment}'."

    content = str(feedback_data.get("post_text", ""))
    timestamp = str(feedback_data.get("timestamp") or get_current_iso_time())
    source = str(feedback_data.get("source", "reddit_post"))

    agent_dir = Path("agents") / agent_id
    profile_path = agent_dir / "profile.json"
    try:
        with profile_path.open("r", encoding="utf-8") as fh:
            profile = json.load(fh)
        archetype = profile.get("archetype_core", "")
    except (OSError, json.JSONDecodeError):
        archetype = ""

    tags = extract_memory_tags(content, archetype)
    sentiment_tag = str(engagement.get("sentiment", "")).lower()
    if sentiment_tag:
        tags.append(sentiment_tag)
    tags = sorted(set(tags))

    entry: Dict = {
        "type": "feedback",
        "source": source,
        "resonance_score": resonance,
        "summary": summary,
        "content": content,
        "tags": tags,
        "timestamp": timestamp,
        "event_id": post_id,
        "priority": resonance,
    }

    log_path = agent_dir / "memory_log.json"
    try:
        if log_path.exists():
            with log_path.open("r", encoding="utf-8") as fh:
                log_entries = json.load(fh)
            if not isinstance(log_entries, list):
                log_entries = []
        else:
            log_entries = []
    except (json.JSONDecodeError, OSError):
        log_entries = []

    log_entries.append(entry)

    try:
        with log_path.open("w", encoding="utf-8") as fh:
            json.dump(log_entries, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass

    tags_path = agent_dir / "memory_tags.json"
    try:
        with tags_path.open("r", encoding="utf-8") as fh:
            tag_map = json.load(fh)
        if not isinstance(tag_map, dict):
            tag_map = {}
    except (json.JSONDecodeError, OSError):
        tag_map = {}

    tag_map[post_id] = tags

    try:
        with tags_path.open("w", encoding="utf-8") as fh:
            json.dump(tag_map, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass
```

## `rwmg/feedback/memory_injector.py`

```python
"""Helpers for converting comment interactions into memory entries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from .resonance_collector import collect_comment_feedback


# feedback/memory_injector.py
def inject_interaction_memory(agent_id: str, comment_data: Dict) -> None:
    """Append ``comment_data`` as a structured memory for ``agent_id``.

    The function stores the memory in ``agents/<agent_id>/memory_log.json`` using
    the standard entry schema.  Failures to read or write the log are silently
    ignored so that simulations can proceed uninterrupted.
    """

    agent_dir = Path("agents") / agent_id
    log_path = agent_dir / "memory_log.json"
    agent_dir.mkdir(parents=True, exist_ok=True)

    try:
        with log_path.open("r", encoding="utf-8") as fh:
            log_entries: List[Dict] = json.load(fh)
            if not isinstance(log_entries, list):
                log_entries = []
    except (OSError, json.JSONDecodeError):
        log_entries = []

    entry = {
        "event_id": comment_data.get("comment_id"),
        "type": "comment_feedback",
        "origin": comment_data.get("origin", ""),
        "timestamp": comment_data.get("timestamp"),
        "content": comment_data.get("body", ""),
        "sentiment": comment_data.get("sentiment"),
        "tags": comment_data.get("tags", []),
        "resonance_score": float(comment_data.get("resonance_score", 0.0)),
    }

    log_entries.append(entry)

    try:
        with log_path.open("w", encoding="utf-8") as fh:
            json.dump(log_entries, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def process_and_log_interactions(agent_id: str, post_id: str, platform: str) -> None:
    """Collect and persist comment interactions for ``post_id``.

    The function orchestrates the comment feedback pipeline by first fetching and
    processing all comments for ``post_id`` and then storing each as a memory via
    :func:`inject_interaction_memory`.
    """

    comments = collect_comment_feedback(agent_id, post_id, platform)
    for comment in comments:
        comment["origin"] = f"{platform}_reply"
        inject_interaction_memory(agent_id, comment)


__all__ = ["inject_interaction_memory", "process_and_log_interactions"]
```

## `rwmg/feedback/post_action_logger.py`

```python
"""Utilities for logging the immediate output of an agent's action.

The wider RWMG system records every post an agent makes so that subsequent
modules – such as feedback processors or explainability tools – can reference
the original content.  This module provides a small helper function to perform
that initial logging step.  The implementation is intentionally
file‑system-based and avoids any external dependencies so that it works in the
contained execution environment used for the unit tests.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Dict

from rwmg.utils.event_bus import emit_event
from rwmg.utils.timestamp_utils import get_current_iso_time


# feedback/post_action_logger.py
def log_agent_output(agent_uuid: str, content: str, platform: str) -> str:
    """Log the raw output of an agent's action and return a unique event ID.

    Parameters
    ----------
    agent_uuid:
        Identifier of the agent that produced the content.
    content:
        The text that was generated or posted by the agent.
    platform:
        Name of the platform the content was intended for.

    Returns
    -------
    str
        The generated ``event_id`` which uniquely identifies this post.
    """

    event_id = uuid.uuid4().hex

    entry: Dict = {
        "event_id": event_id,
        "timestamp": get_current_iso_time(),
        "content": content,
        "platform": platform,
        # Resonance and feedback will be filled in by later stages of the
        # feedback pipeline.  Initial defaults allow downstream code to rely on
        # these keys being present.
        "resonance_score": 0.0,
        "injected_memories": [],
        "feedback_data_ref": "",
    }

    agent_dir = Path("agents") / agent_uuid
    log_path = agent_dir / "memory_log.json"
    agent_dir.mkdir(parents=True, exist_ok=True)

    try:
        if log_path.exists():
            with log_path.open("r", encoding="utf-8") as fh:
                log_entries = json.load(fh)
            if not isinstance(log_entries, list):
                log_entries = []
        else:
            log_entries = []
    except (json.JSONDecodeError, OSError):
        log_entries = []

    log_entries.append(entry)

    try:
        with log_path.open("w", encoding="utf-8") as fh:
            json.dump(log_entries, fh, ensure_ascii=False, indent=2)
    except OSError:
        # Logging should be best-effort; failure to write the log is swallowed so
        # that the simulation can continue.  The event ID is still returned to
        # the caller even though the entry could not be persisted.
        pass

    # Notify interested parties that a post has been logged.  Errors from
    # subscriber handlers are intentionally ignored so the logging path remains
    # robust.
    try:  # pragma: no cover - event bus errors are non-critical
        emit_event("post_logged", {"agent_uuid": agent_uuid, "event_id": event_id})
    except Exception:  # pragma: no cover
        pass

    return event_id


```

## `rwmg/feedback/prompt_tuner.py`

```python
"""Adaptive prompt tuning based on historical resonance feedback."""
from __future__ import annotations

from typing import Dict, List


def _clamp(value: float, minimum: float = -1.0, maximum: float = 1.0) -> float:
    """Clamp ``value`` to the inclusive range ``[minimum, maximum]``."""
    return max(minimum, min(maximum, value))


def tune_prompt_parameters(agent_id: str, memory_log: List[Dict], current_state: Dict) -> Dict:
    """Adjust an agent's state according to recent resonance feedback.

    Parameters
    ----------
    agent_id:
        Identifier of the agent being tuned.  Currently unused but retained for
        future extensibility.
    memory_log:
        Parsed contents of ``memory_log.json`` for the agent.  The function
        expects an iterable of memory dictionaries where feedback entries contain
        at least ``"type"`` and ``"resonance_score"`` fields and optionally
        ``"tone"`` or ``"topic"`` hints.
    current_state:
        The agent's current state as loaded from ``agent_state.json``.

    Returns
    -------
    Dict
        A modified copy of ``current_state`` with updated emotional vectors,
        tone biases, topic preferences and risk profile.
    """

    # Ensure expected structures exist
    emotional = current_state.setdefault("emotional_vector", {})
    tone_bias = current_state.setdefault("tone_bias", {})
    topic_weights = current_state.setdefault("topic_preference_weights", {})

    feedback_memories = [m for m in memory_log if m.get("type") == "feedback"]
    if not feedback_memories:
        return current_state

    tone_scores: Dict[str, List[float]] = {}
    topic_scores: Dict[str, List[float]] = {}

    for mem in feedback_memories:
        resonance = float(mem.get("resonance_score", 0.0))
        tone = mem.get("tone")
        topic = mem.get("topic")
        if tone:
            tone_scores.setdefault(tone, []).append(resonance)
        if topic:
            topic_scores.setdefault(topic, []).append(resonance)

    def _average(scores: List[float]) -> float:
        return sum(scores) / len(scores) if scores else 0.0

    # Tone trend detection
    for tone, scores in tone_scores.items():
        avg = _average(scores)
        if avg > 0.7:
            tone_bias[tone] = tone_bias.get(tone, 0.0) + 0.2
            emotional["valence"] = _clamp(float(emotional.get("valence", 0.0)) + 0.1)
            emotional["arousal"] = _clamp(float(emotional.get("arousal", 0.0)) + 0.2)
        elif avg < 0.3:
            tone_bias[tone] = tone_bias.get(tone, 0.0) - 0.2

    # Topic reinforcement
    for topic, scores in topic_scores.items():
        avg = _average(scores)
        if avg > 0.6:
            topic_weights[topic] = topic_weights.get(topic, 0.0) + 0.3
        elif avg < 0.4:
            topic_weights[topic] = topic_weights.get(topic, 0.0) - 0.2

    # Risk profile adjustment based on recent performance
    recent_scores = [float(m.get("resonance_score", 0.0)) for m in feedback_memories[-3:]]
    underperforming = sum(1 for s in recent_scores if s < 0.5)
    current_state["risk_profile"] = "low" if underperforming >= 2 else "balanced"

    return current_state


__all__ = ["tune_prompt_parameters"]
```

## `rwmg/feedback/resonance_collector.py`

```python
"""Utilities for collecting post feedback from social platforms.

The real RWMG system would reach out to the respective platform APIs to
retrieve engagement metrics and raw comments for a post.  To keep the test
environment lightweight and deterministic this implementation falls back to
reading JSON files from ``post_url`` when available and otherwise returns an
empty data structure.  The goal is to normalise disparate feedback formats into
the canonical schema described in the project brief so that downstream modules
can attribute resonance scores.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Dict, List

try:  # pragma: no cover - import is trivial but may fail in minimal envs
    import requests
except Exception:  # pragma: no cover
    requests = None


_POSITIVE_WORDS = {
    "good",
    "great",
    "love",
    "excellent",
    "happy",
    "joy",
    "nice",
    "like",
}

_NEGATIVE_WORDS = {
    "bad",
    "terrible",
    "hate",
    "angry",
    "sad",
    "awful",
    "dislike",
}


def _basic_sentiment(text: str) -> float:
    """Compute a naive sentiment score in the range ``[-1, 1]``.

    The heuristic simply counts occurrences of words from small positive and
    negative vocabularies and normalises by the total number of words.  It is by
    no means a sophisticated sentiment analyser but suffices for the unit tests
    and keeps the project free from heavy dependencies.
    """

    tokens = re.findall(r"[A-Za-z']+", text.lower())
    if not tokens:
        return 0.0

    score = sum(1 for t in tokens if t in _POSITIVE_WORDS) - sum(
        1 for t in tokens if t in _NEGATIVE_WORDS
    )
    return max(-1.0, min(1.0, score / len(tokens)))


def _extract_tags(text: str) -> List[str]:
    """Derive a small set of keyword tags from ``text``.

    The implementation purposely remains extremely lightweight.  It simply
    returns the unique alphanumeric words longer than four characters which
    appear in the text.  The result is capped at five tags to keep subsequent
    memory entries concise.
    """

    tokens = re.findall(r"[A-Za-z']+", text.lower())
    tags = sorted({t for t in tokens if len(t) > 4})
    return tags[:5]


def _compute_comment_resonance(comment: Dict, sentiment: float) -> float:
    """Compute a naive resonance score for a single comment.

    The score combines the upvote count with the sentiment polarity.  It is not
    meant to be a perfect measure but provides a deterministic value in the
    ``[0, 1]`` range for tests and downstream weighting functions.
    """

    upvotes = float(comment.get("ups", comment.get("score", 0)))
    base = upvotes / (upvotes + 10.0)  # normalise vote influence
    resonance = base * (1.0 + (sentiment * 0.5))
    return max(0.0, min(1.0, resonance))


def _fetch_comments_from_api(post_id: str, platform: str) -> List[Dict]:
    """Fetch raw comments for ``post_id``.

    Similar to :func:`collect_feedback`, this helper first attempts to treat the
    ``post_id`` as a local JSON file path containing a ``"comments"`` array.  If
    the file is not present it falls back to an HTTP ``GET`` when the optional
    :mod:`requests` dependency is available.  Network failures simply yield an
    empty list.
    """

    path = Path(post_id)
    data: Dict = {}
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            data = {}
    elif requests is not None:  # pragma: no cover - network disabled in tests
        try:
            resp = requests.get(post_id, timeout=5)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            data = {}

    comments = data.get("comments", [])
    if not isinstance(comments, list):
        return []
    return comments


# feedback/resonance_collector.py
def collect_feedback(post_url: str, platform: str, agent_manifest: Dict) -> Dict:
    """Normalise feedback metrics for a previously logged post.

    Parameters
    ----------
    post_url:
        Location of the post.  For the purposes of the tests this may point to a
        local JSON file containing mock feedback data.
    platform:
        Name of the social platform the post was published on.  Currently only
        used for bookkeeping but retained for future extensibility.
    agent_manifest:
        Mapping of agent identifiers to their metadata.  Used to determine which
        comments originate from other agents so that the human/agent ratio can be
        computed.

    Returns
    -------
    Dict
        A dictionary following the ``feedback/feedback_data.json`` schema.
    """

    raw_data: Dict = {}
    path = Path(post_url)

    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                raw_data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            raw_data = {}
    else:
        # Fallback to an HTTP GET; failures simply yield an empty dataset.
        if requests is not None:  # pragma: no cover - network disabled in tests
            try:
                resp = requests.get(post_url, timeout=5)
                resp.raise_for_status()
                raw_data = resp.json()
            except Exception:
                raw_data = {}

    likes = int(raw_data.get("likes", 0))
    shares = int(raw_data.get("shares", 0))
    comments: List[Dict] = raw_data.get("comments", []) or []

    agent_ids = set(agent_manifest.keys())
    agent_names = {
        v.get("name") for v in agent_manifest.values() if isinstance(v, dict)
    }

    human_comments = 0
    agent_engagement = 0
    sentiments: List[float] = []

    for comment in comments:
        text = str(comment.get("text", ""))
        author = str(comment.get("author", ""))
        sentiments.append(_basic_sentiment(text))
        if author in agent_ids or author in agent_names:
            agent_engagement += 1
        else:
            human_comments += 1

    total_replies = len(comments)
    human_ratio = human_comments / total_replies if total_replies else 0.0
    human_ratio = max(0.0, min(1.0, human_ratio))

    avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0
    avg_sentiment = max(-1.0, min(1.0, avg_sentiment))

    # Persist the raw feedback for traceability.  Failures are non-critical; an
    # empty reference path signals that the data could not be stored.
    feedback_dir = Path("feedback")
    feedback_dir.mkdir(exist_ok=True)
    raw_ref = ""
    try:
        ref_path = feedback_dir / f"raw_{uuid.uuid4().hex}.json"
        with ref_path.open("w", encoding="utf-8") as fh:
            json.dump(raw_data, fh, ensure_ascii=False, indent=2)
        raw_ref = str(ref_path)
    except OSError:
        raw_ref = ""

    return {
        "post_url": post_url,
        "platform": platform,
        "total_likes": likes,
        "total_shares": shares,
        "total_replies": total_replies,
        "human_comment_ratio": human_ratio,
        "average_sentiment_score": avg_sentiment,
        "agent_engagement_count": agent_engagement,
        "raw_feedback_ref": raw_ref,
    }


def collect_comment_feedback(agent_id: str, post_id: str, platform: str) -> List[Dict]:
    """Return processed feedback for individual comments on ``post_id``.

    Parameters
    ----------
    agent_id:
        Identifier of the agent that authored the original post.  Currently
        unused but reserved for future filtering of self-replies.
    post_id:
        Identifier or path of the post to retrieve comments for.  In the test
        environment this may be a local JSON file path.
    platform:
        Name of the platform the post was published on.  Included for
        completeness and added to the resulting ``origin`` field by the caller.
    """

    comments = _fetch_comments_from_api(post_id, platform)
    processed: List[Dict] = []
    for comment in comments:
        body = str(comment.get("body") or comment.get("text") or "")
        sentiment_score = _basic_sentiment(body)
        if sentiment_score > 0.2:
            sentiment_label = "positive"
        elif sentiment_score < -0.2:
            sentiment_label = "negative"
        else:
            sentiment_label = "neutral"

        tags = _extract_tags(body)
        resonance = _compute_comment_resonance(comment, sentiment_score)

        processed.append(
            {
                "comment_id": str(comment.get("id") or uuid.uuid4().hex),
                "timestamp": comment.get("created_utc"),
                "author": comment.get("author"),
                "sentiment": sentiment_label,
                "tags": tags,
                "resonance_score": resonance,
                "body": body,
            }
        )

    return processed

```

## `rwmg/feedback/weight_attributor.py`

```python
"""Utilities for attributing weight to feedback memories.

At the moment only :func:`compute_memory_weight` is implemented.  The rest of the
module intentionally remains minimal as the project skeleton only requires the
core scoring logic.  The function follows the detailed specification provided in
the project brief and converts raw engagement feedback into a normalised
resonance score between ``0.0`` and ``1.0``.
"""

from pathlib import Path
from typing import Dict

import yaml


def _load_event_multiplier(event_type: str) -> float:
    """Fetch the multiplier for ``event_type`` from ``archetype_rules.yaml``.

    If the configuration file or the specific event type cannot be found the
    function gracefully falls back to ``1.0``.
    """

    config_path = Path(__file__).resolve().parents[1] / "config" / "archetype_rules.yaml"
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            rules = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return 1.0

    return float(rules.get(event_type, {}).get("event_type_multiplier", 1.0))


# feedback/weight_attributor.py
def compute_memory_weight(
    feedback_data: Dict,
    event_type: str,
    agent_state: Dict,
    platform_profile: Dict,
) -> float:
    """Calculate a resonance score for a memory.

    The score is a product of several components:

    * **Raw score** – weighted sum of likes, comments and shares according to
      the platform configuration.
    * **Authenticity multiplier** – proportion of human comments vs. agent
      noise.
    * **Emotional alignment factor** – adjusts the score based on sentiment of
      the received feedback.  Sentiment is expected in the range ``[-1, 1]`` and
      scales the score linearly by ``±0.5``.
    * **Event type multiplier** – amplifies the score based on thematic
      importance defined in ``archetype_rules.yaml``.

    Finally the score is normalised to the ``0.0`` – ``1.0`` range using a
    simple saturation function ``x / (1 + x)``.
    """

    # --- 1. Raw score -----------------------------------------------------
    likes = float(feedback_data.get("total_likes", 0))
    comments = float(feedback_data.get("total_replies", 0))
    shares = float(feedback_data.get("total_shares", 0))

    raw_score = (
        likes * float(platform_profile.get("like_weight", 0.0))
        + comments * float(platform_profile.get("comment_weight", 0.0))
        + shares * float(platform_profile.get("share_weight", 0.0))
    )

    # --- 2. Authenticity multiplier --------------------------------------
    authenticity_multiplier = float(
        max(0.0, min(1.0, feedback_data.get("human_comment_ratio", 0.0)))
    )

    # --- 3. Emotional alignment factor -----------------------------------
    # Feedback sentiment is provided in ``[-1, 1]`` and directly adjusts the
    # score. Positive sentiment boosts the score while negative sentiment
    # dampens it, with a maximum effect of ±50%.
    sentiment = float(feedback_data.get("average_sentiment_score", 0.0))
    sentiment = max(-1.0, min(1.0, sentiment))
    emotional_alignment_factor = 1.0 + (sentiment * 0.5)

    # --- 4. Event type multiplier ----------------------------------------
    event_multiplier = _load_event_multiplier(event_type)

    # --- 5. Final resonance score ----------------------------------------
    final_score = (
        raw_score
        * authenticity_multiplier
        * emotional_alignment_factor
        * event_multiplier
    )

    # --- 6. Normalisation -------------------------------------------------
    if final_score <= 0:
        return 0.0

    normalised_score = final_score / (1.0 + final_score)
    return float(max(0.0, min(1.0, normalised_score)))
```

## `rwmg/lifecycle_manager/birth_protocol.py`

```python
"""Activate a newly generated agent by writing its persona to disk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

from rwmg.sim_runner.sim_start import populate_manifest


def activate_new_agent(persona_data: Dict) -> str:
    """Persist the supplied persona data and return the agent UUID.

    The function expects ``persona_data`` to contain at least a ``profile``
    mapping following the ``profile.json`` schema and may include additional
    files such as ``agent_state`` and ``canonical_events``.  Missing files are
    created with sensible defaults so that downstream components can operate.
    """

    profile = persona_data.get("profile", {})
    agent_id = profile.get("agent_id")
    if not agent_id:
        raise ValueError("persona_data must include a profile with 'agent_id'")

    root = Path("agents")
    agent_dir = root / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "profile.json": profile,
        "agent_state.json": persona_data.get("agent_state", {}),
        "canonical_events.json": persona_data.get("canonical_events", []),
        "memory_log.json": [],
        "memory_cache_top5.json": [],
        "suppression_log.json": [],
        "memory_tags.json": {},
        "connections.json": {"friends": [], "mentors": []},
    }

    for filename, payload in files.items():
        try:
            with (agent_dir / filename).open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # Ensure subordinate directories exist
    (agent_dir / "temp").mkdir(exist_ok=True)
    (agent_dir / "memory_index.csv").touch(exist_ok=True)

    # Add entry to global manifest
    populate_manifest([agent_id])

    return agent_id


__all__ = ["activate_new_agent"]

```

## `rwmg/lifecycle_manager/evolution_protocol.py`

```python
"""Detect long-term psychological shifts in agents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional


def check_for_evolution(agent_uuid: str) -> Optional[Dict]:
    """Inspect an agent's memory log for signs of evolution.

    The heuristic here is deliberately small: if the average resonance score of
    stored memories exceeds ``0.8`` we signal a potential evolution event and
    return a dictionary describing the trigger.  Otherwise ``None`` is returned.
    """

    log_path = Path("agents") / agent_uuid / "memory_log.json"
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None

    if not entries:
        return None

    avg = sum(float(e.get("resonance_score", 0.0)) for e in entries) / len(entries)
    if avg > 0.8:
        return {"agent_id": agent_uuid, "trigger": "high_resonance", "average": avg}
    return None


__all__ = ["check_for_evolution"]

```

## `rwmg/lifecycle_manager/main_loop.py`

```python
"""Core daily routine executed for each active agent."""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import yaml

import yaml

from rwmg.feedback.post_action_logger import log_agent_output

from rwmg.feedback.prompt_tuner import tune_prompt_parameters

from rwmg.lifecycle_manager.output_sanitizer import sanitize_output

from rwmg.lifecycle_manager.prompt_engine.inject_memory_context import (
    format_memory_context,
)
from rwmg.lifecycle_manager.prompt_engine.prompt_builder import build_prompt
from rwmg.lifecycle_manager.prompt_engine.prompt_logger import log_injected_memories
from rwmg.lifecycle_manager.prompt_engine.tone_selector import select_tone
from rwmg.social.community_discovery import choose_target_community
from rwmg.utils.api_wrappers import (
    _load_platform_keys,
    call_gemini_api,
    post_to_platform,
)
from rwmg.utils.memory_extractor import rank_memories
from rwmg.feedback.memory_injector import process_and_log_interactions


def _load_behavior_profiles() -> Dict[str, Dict]:
    config_path = Path("config") / "agent_behavior_profiles.yaml"
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


BEHAVIOR_PROFILES = _load_behavior_profiles()


def log_violation(agent_id: str, result: Dict, content: str = "") -> None:
    """Persist a record of blocked content for offline review."""
    log_dir = Path("quarantine_log")
    log_dir.mkdir(exist_ok=True)
    timestamp = datetime.utcnow().isoformat().replace(":", "-")
    entry = {"agent_id": agent_id, "content": content, **result}
    file_path = log_dir / f"{agent_id}_{timestamp}.json"
    try:
        with file_path.open("w", encoding="utf-8") as fh:
            json.dump(entry, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def retry_prompt_with_constraints(agent_id: str, reason: List[str]) -> None:
    """Placeholder hook to re-run generation with additional constraints."""
    # In this reference implementation we simply record the failure and return.
    _ = (agent_id, reason)
    return


def _frequency_allows_post(freq: str) -> bool:
    if freq == "daily":
        return True
    if freq == "random":
        return random.random() < 0.5
    match = re.match(r"(\d+)x/week", str(freq))
    if match:
        count = int(match.group(1))
        return random.random() < count / 7
    match = re.match(r"(\d+)x/day", str(freq))
    if match:
        count = int(match.group(1))
        return random.random() < min(1.0, count)
    return True


def _within_activity_window(window: str, now: datetime) -> bool:
    hour = now.hour
    if window in (None, "all_day"):
        return True
    if window in ("morning", "fixed_morning"):
        return 6 <= hour < (9 if window == "fixed_morning" else 12)
    if window == "midday":
        return 11 <= hour < 14
    if window == "afternoon":
        return 12 <= hour < 17
    if window == "evening":
        return 17 <= hour < 22
    if window == "night":
        return hour >= 22 or hour < 6
    if window == "random":
        return random.random() < 0.5
    if window == "sporadic":
        return random.random() < 0.25
    return True


def _should_post(agent_state: Dict, profile: Dict, now: datetime) -> bool:
    next_time = agent_state.get("next_post_time")
    if next_time:
        try:
            if now < datetime.fromisoformat(str(next_time)):
                return False
        except ValueError:
            pass

    if not _within_activity_window(profile.get("activity_windows"), now):
        return False

    if not _frequency_allows_post(profile.get("post_frequency")):
        return False

    return True


def run_agent_day(agent_uuid: str, current_day: int, proxies: Optional[Dict[str, str]] = None) -> None:
    """Execute the posting and memory update cycle for ``agent_uuid``.

    The implementation is intentionally lightweight for the unit tests; it
    prepares a prompt from the agent's highest weighted memories, obtains model
    output and logs the result as a new memory event.
    """

    agent_dir = Path("agents") / agent_uuid
    state_path = agent_dir / "agent_state.json"
    try:
        with state_path.open("r", encoding="utf-8") as fh:
            agent_state: Dict = json.load(fh)
    except (OSError, json.JSONDecodeError):
        agent_state = {}


    # Load platform profiles and choose the first available platform
    try:
        import yaml

        with open("config/platform_profiles.yaml", "r", encoding="utf-8") as fh:
            profiles = yaml.safe_load(fh) or {}
    except Exception:
        profiles = {}

    platform_name, profile = next(iter(profiles.items()), ("twitter", {}))
    profile = dict(profile or {})
    profile["platform"] = platform_name

    profile_name = agent_state.get("behavior_profile", "")
    behaviour = BEHAVIOR_PROFILES.get(profile_name, {})
    now = datetime.utcnow()
    if not _should_post(agent_state, behaviour, now):
        return


    # Update memory rankings to refresh cache files
    rank_memories(agent_uuid)

    cache_path = agent_dir / "memory_cache_top5.json"
    try:
        with cache_path.open("r", encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, json.JSONDecodeError):
        cache = []

    community = agent_state.get("target_subreddit") or profile.get("community")
    memory_context, style_examples = format_memory_context(cache, community)
    memory_fragments = [memory_context] if memory_context else []
    memory_ids = [entry.get("event_id") for entry in cache if entry.get("event_id")]
    memory_weights = [entry.get("content", "") for entry in cache]

    # Load persona metadata for community targeting
    try:
        persona_path = agent_dir / "persona_meta.yaml"
        with persona_path.open("r", encoding="utf-8") as fh:
            persona_meta = yaml.safe_load(fh) or {}
    except Exception:
        persona_meta = {}

    target_subreddit = choose_target_community(
        agent_uuid, persona_meta, memory_weights
    )

    # Tune state based on recent feedback before constructing the prompt
    memory_log_path = agent_dir / "memory_log.json"
    try:
        with memory_log_path.open("r", encoding="utf-8") as fh:
            memory_log = json.load(fh)
    except (OSError, json.JSONDecodeError):
        memory_log = []


    agent = tune_prompt_parameters(agent_uuid, memory_log, agent_state)
    try:
        with state_path.open("w", encoding="utf-8") as fh:
            json.dump(agent_state, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass

    tone = select_tone(
        agent_state.get("emotional_vector", {}), profile.get("community_tone")
    )

    prompt, platform = build_prompt(
        agent_uuid, agent_state, memory_fragments, tone, profile, style_examples
    )


    tone = select_tone(agent_state.get("emotional_vector", {}))

    # Load platform profiles and choose the first available platform
    try:
        with open("config/platform_profiles.yaml", "r", encoding="utf-8") as fh:
            profiles = yaml.safe_load(fh) or {}
    except Exception:
        profiles = {}


    platform_name, profile = next(iter(profiles.items()), ("twitter", {}))
    profile = dict(profile or {})
    profile["platform"] = platform_name
    if target_subreddit:
        profile["target_subreddit"] = target_subreddit

    platform_name, platform_profile = next(iter(profiles.items()), ("twitter", {}))
    platform_profile = dict(platform_profile or {})
    platform_profile["platform"] = platform_name


    prompt, platform = build_prompt(
        agent_uuid,
        agent_state,
        memory_fragments,
        tone,
        platform_profile,
        style_examples,
    )

    try:
        raw_output = call_gemini_api(prompt, proxies=proxies)
    except Exception:
        raw_output = f"{agent_uuid[:8]} placeholder post"

    sanitization_result = sanitize_output(
        raw_output, {"agent_id": agent_uuid, "platform": platform}
    )
    if not sanitization_result["passed"]:
        log_violation(agent_uuid, sanitization_result, raw_output)
        retry_prompt_with_constraints(agent_uuid, sanitization_result["violations"])
        return

    event_id = log_agent_output(agent_uuid, raw_output, platform)
    log_injected_memories(agent_uuid, event_id, memory_ids)
    process_and_log_interactions(agent_uuid, event_id, platform)

    try:
        token_map = _load_platform_keys()
        auth_token = token_map.get(f"{platform}_token", "")
        if auth_token:
            post_to_platform(platform, raw_output, auth_token, proxies=proxies)
    except Exception:
        pass

    agent_state["last_post_timestamp"] = now.isoformat()
    cooldown = behaviour.get("cooldown_period_hours", [24, 24])
    try:
        min_cd, max_cd = int(cooldown[0]), int(cooldown[1])
    except Exception:
        min_cd, max_cd = 24, 24
    hours = random.randint(min_cd, max_cd)
    agent_state["next_post_time"] = (now + timedelta(hours=hours)).isoformat()

    try:
        with state_path.open("w", encoding="utf-8") as fh:
            json.dump(agent_state, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


__all__ = ["run_agent_day"]

```

## `rwmg/lifecycle_manager/output_sanitizer.py`

```python
"""Utilities for validating LLM output before publication."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional


_TOXIC_PATTERNS = [
    r"\bhate\b",
    r"\bkill\b",
    r"\bidiot\b",
]


def _check_toxicity(content: str) -> List[str]:
    violations: List[str] = []
    for pattern in _TOXIC_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            word = pattern.strip("\\b")
            violations.append(f"Contains disallowed term: {word}")
    return violations


def _check_factuality(content: str) -> List[str]:
    violations: List[str] = []
    for match in re.findall(r"\b(\d{4})\b", content):
        year = int(match)
        if year < 1900 or year > 2100:
            violations.append(f"Suspicious year: {year}")
    misinfo_phrases = ["earth is flat", "moon is made of cheese", "2+2=5"]
    lower = content.lower()
    for phrase in misinfo_phrases:
        if phrase in lower:
            violations.append(f"Factual error detected: '{phrase}'")
    return violations


def _load_previous_posts(agent_id: str) -> List[str]:
    agent_dir = Path("agents") / agent_id
    log_path = agent_dir / "memory_log.json"
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
        return [str(entry.get("content", "")) for entry in entries if isinstance(entry, dict)]
    except Exception:
        return []


def _check_plagiarism(content: str, context: Dict[str, str]) -> List[str]:
    agent_id = context.get("agent_id") if context else None
    if not agent_id:
        return []
    violations: List[str] = []
    for previous in _load_previous_posts(agent_id):
        if not previous:
            continue
        similarity = SequenceMatcher(None, previous, content).ratio()
        if similarity > 0.9:
            violations.append("Content too similar to previous post")
            break
    return violations


def sanitize_output(content: str, context: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    """Run safety, factuality and originality checks on ``content``."""
    result = {"passed": True, "violations": [], "suggestion": None}
    context = context or {}

    violations: List[str] = []
    violations.extend(_check_toxicity(content))
    violations.extend(_check_factuality(content))
    violations.extend(_check_plagiarism(content, context))

    if violations:
        result["passed"] = False
        result["violations"] = violations

    return result


__all__ = ["sanitize_output"]

```

## `rwmg/lifecycle_manager/prompt_engine/comment_style_mimicry.py`

```python
"""Utilities for mimicking subreddit comment style."""

from __future__ import annotations

import requests
from typing import List


REDDIT_BASE_URL = "https://www.reddit.com"
USER_AGENT = "social-simulator/0.1"


def fetch_comment_style_examples(subreddit: str, limit: int = 5) -> List[str]:
    """Return high-scoring comments from ``subreddit``.

    Parameters
    ----------
    subreddit:
        Name of the subreddit to sample from.
    limit:
        Maximum number of comment examples to return.

    The function queries Reddit's public JSON endpoints without authentication.
    It gracefully falls back to an empty list if requests fail or the payload
    does not contain the expected structure.
    """

    if not subreddit:
        return []

    headers = {"User-Agent": USER_AGENT}
    examples: List[str] = []
    try:
        listing = requests.get(
            f"{REDDIT_BASE_URL}/r/{subreddit}/hot.json?limit={limit}",
            headers=headers,
            timeout=10,
        ).json()
        posts = listing.get("data", {}).get("children", [])
        for post in posts:
            post_id = post.get("data", {}).get("id")
            if not post_id:
                continue
            thread = requests.get(
                f"{REDDIT_BASE_URL}/r/{subreddit}/comments/{post_id}.json?sort=top&limit=1",
                headers=headers,
                timeout=10,
            ).json()
            comments = thread[1].get("data", {}).get("children", []) if len(thread) > 1 else []
            if not comments:
                continue
            body = comments[0].get("data", {}).get("body")
            if body:
                examples.append(body.strip())
            if len(examples) >= limit:
                break
    except Exception:
        return []
    return examples[:limit]


__all__ = ["fetch_comment_style_examples"]
```

## `rwmg/lifecycle_manager/prompt_engine/inject_memory_context.py`

```python
"""Helpers for injecting memory context into prompts.

The real system crafts a natural language summary of the most salient memories
to provide situational awareness to the language model.  For testing we employ a
simple formatter that joins memory snippets into a bullet list.
"""

from __future__ import annotations

from typing import Iterable, List, Dict, Optional, Tuple

from .comment_style_mimicry import fetch_comment_style_examples


def format_memory_context(
    memory_cache: Dict, subreddit: Optional[str] = None
) -> Tuple[str, List[str]]:
    """Return a natural language block and style examples.

    Parameters
    ----------
    memory_cache:
        Expected to be a sequence of mappings with a ``"content"`` field as
        produced by :func:`utils.memory_extractor.rank_memories`.
    subreddit:
        If provided, top comments from this subreddit will be retrieved and
        appended as style examples for prompt conditioning.
    """

    if not memory_cache and not subreddit:
        return "", []

    fragments: Iterable[str] = [
        entry.get("content", "") for entry in memory_cache if entry.get("content")
    ]
    lines: List[str] = [f"- {frag}" for frag in fragments if frag]

    examples: List[str] = []
    if subreddit:
        examples = fetch_comment_style_examples(subreddit)
        if examples:
            lines.append("Community style examples:")
            lines.extend([f"- {ex}" for ex in examples])

    return "\n".join(lines), examples


__all__ = ["format_memory_context"]

```

## `rwmg/lifecycle_manager/prompt_engine/prompt_builder.py`

```python
"""Prompt construction utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional


def build_prompt(
    agent_id: str,
    agent_state: Dict,
    memory_fragments: List[str],
    tone: str,
    platform_profile: Dict,
    style_examples: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """Assemble a prompt for the agent's next post.

    The function is intentionally lightweight; it merely combines the supplied
    pieces into a textual instruction that can be sent to an LLM.  The second
    element of the returned tuple is the target platform name as provided in the
    ``platform_profile`` mapping under the ``"platform"`` key.
    """

    platform = platform_profile.get("platform", "")
    style = platform_profile.get("style", "")
    max_tokens = platform_profile.get("max_tokens", 280)

    if not memory_fragments:
        log_path = Path("agents") / agent_id / "memory_log.json"
        try:
            with log_path.open("r", encoding="utf-8") as fh:
                log_entries = json.load(fh) or []
        except (OSError, json.JSONDecodeError):
            log_entries = []
        memory_fragments = [
            entry.get("content", "")
            for entry in log_entries[-5:]
            if entry.get("content")
        ]

    memory_context = "\n".join(memory_fragments) if memory_fragments else ""
    style_block = "\n".join(style_examples or [])
    prompt = (
        f"You are agent {agent_id}. Write a {style} post for {platform} "
        f"in a {tone} tone. Stay within {max_tokens} characters."
    )
    if memory_context:
        prompt += f"\nConsider these memories:\n{memory_context}"
    if style_block:
        prompt += (
            "\nRespond to this post in the style of the following comments from the subreddit:\n"
            f"{style_block}"
        )

    return prompt, platform


__all__ = ["build_prompt"]

```

## `rwmg/lifecycle_manager/prompt_engine/prompt_logger.py`

```python
"""Logging helpers for prompt memory injection.

This module keeps track of which memories influenced a generated post so that
later analysis modules can reference them.  The information is stored within the
agent's ``memory_log.json`` entries under the ``"injected_memories"`` field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List


def log_injected_memories(agent_uuid: str, event_id: str, injected_memory_ids: List[str]) -> None:
    """Persist the list of memory identifiers used for a prompt.

    Parameters
    ----------
    agent_uuid:
        Identifier of the agent that generated the post.
    event_id:
        The unique identifier of the post event returned by
        :func:`feedback.post_action_logger.log_agent_output`.
    injected_memory_ids:
        List of memory ``event_id`` values that were inserted into the prompt
        context.
    """

    log_path = Path("agents") / agent_uuid / "memory_log.json"
    try:
        with log_path.open("r", encoding="utf-8") as fh:
            entries = json.load(fh)
    except (OSError, json.JSONDecodeError):
        entries = []

    # Find the log entry for the event and update its injected memories.  If no
    # entry exists we create a minimal placeholder so downstream modules can
    # still reference the association.
    found = False
    for entry in entries:
        if entry.get("event_id") == event_id:
            entry["injected_memories"] = list(injected_memory_ids)
            found = True
            break

    if not found:
        entries.append(
            {
                "event_id": event_id,
                "timestamp": "",
                "content": "",
                "platform": "",
                "resonance_score": 0.0,
                "injected_memories": list(injected_memory_ids),
                "feedback_data_ref": "",
            }
        )

    try:
        with log_path.open("w", encoding="utf-8") as fh:
            json.dump(entries, fh, ensure_ascii=False, indent=2)
    except OSError:
        # Logging failures should not be fatal for the simulation loop.
        pass


__all__ = ["log_injected_memories"]

```

## `rwmg/lifecycle_manager/prompt_engine/tone_selector.py`

```python
"""Tone selection based on the agent's emotional state."""

from __future__ import annotations

from typing import Dict, Optional


def select_tone(emotional_vector: Dict, community_tone: Optional[str] = None) -> str:
    """Choose a writing tone derived from emotional state and community cues.

    The heuristic is intentionally simple: ``valence`` influences whether the
    tone is positive or negative, while ``arousal`` modulates the energy level.
    If values are missing the function falls back to a neutral tone.  When
    ``community_tone`` is provided it is prefixed to the computed tone to blend
    the agent's state with community expectations.
    """

    valence = float(emotional_vector.get("valence", 0.5))
    arousal = float(emotional_vector.get("arousal", 0.5))

    if valence >= 0.6:
        base = "warm"
    elif valence <= 0.4:
        base = "somber"
    else:
        base = "neutral"

    if arousal > 0.6:
        modifier = "energetic"
    elif arousal < 0.4:
        modifier = "calm"
    else:
        modifier = "steady"

    if base == "neutral" and modifier in {"steady", "calm"}:
        tone = "conversational"
    else:
        tone = f"{modifier} {base}".strip()
    if community_tone:
        return f"{community_tone} {tone}".strip()
    return tone


__all__ = ["select_tone"]

```

## `rwmg/lifecycle_manager/ritual_tracker.py`

```python
"""Simple helpers to track an agent's ritual status."""

from __future__ import annotations

import json
from pathlib import Path


def _load_state(agent_uuid: str) -> dict:
    path = Path("agents") / agent_uuid / "agent_state.json"
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(agent_uuid: str, state: dict) -> None:
    path = Path("agents") / agent_uuid / "agent_state.json"
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def start_ritual(agent_uuid: str, stage: str) -> None:
    state = _load_state(agent_uuid)
    state["is_in_ritual"] = True
    state["ritual_stage"] = stage
    _save_state(agent_uuid, state)


def complete_ritual(agent_uuid: str) -> None:
    state = _load_state(agent_uuid)
    state["is_in_ritual"] = False
    state["ritual_stage"] = ""
    _save_state(agent_uuid, state)


__all__ = ["start_ritual", "complete_ritual"]

```

## `rwmg/memory_loop.py`

```python
"""Phase 6 meta-policy self-modeling layer for the memory loop.

The loop remains intentionally isolated:

retrieve candidates -> compare strategies and model variants -> counterfactual
attribution -> regret/meta-error-aware selection -> generate -> evaluate
-> update -> causal credit -> update self-model -> persist trace

``memory_store.json`` is the single canonical store for event state, cached
embeddings, expected values, strategy clusters, policy state, self-model
calibration data, and traces.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


DEFAULT_AGENT_ID = "default"
DEFAULT_CLUSTER_THRESHOLD = 0.58
DEFAULT_EPSILON = 0.2
DEFAULT_GAMMA = 0.5
DEFAULT_LEARNING_RATE = 0.35
DEFAULT_MAX_MEMORIES = 3
DEFAULT_MAX_WEIGHT = 1.0
DEFAULT_MEMORY_TOKEN_CAP = 120
DEFAULT_MIN_WEIGHT = -1.0
DEFAULT_RETENTION_FACTOR = 0.92
DEFAULT_TEMPORAL_WINDOW = 4
DEFAULT_THRESHOLD = 0.05
EXPLORATION_MIN = 0.05
EXPLORATION_MAX = 0.4
EXPLORATION_VARIANCE_SCALE = 4.0
REGRET_WEIGHT = 0.35
META_ERROR_WEIGHT = 0.25

MODEL_VARIANTS = [
    "baseline",
    "low_bias",
    "high_sensitivity",
    "low_regret_weight",
    "high_regret_weight",
]

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "for",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "with",
}

_CONCEPT_ALIASES = {
    "automobile": "vehicle",
    "auto": "vehicle",
    "car": "vehicle",
    "vehicle": "vehicle",
    "brief": "summarize",
    "condense": "summarize",
    "recap": "summarize",
    "summary": "summarize",
    "summarize": "summarize",
    "compose": "write",
    "create": "write",
    "draft": "write",
    "write": "write",
    "effective": "quality",
    "improve": "quality",
    "improves": "quality",
    "quality": "quality",
    "random": "noise",
    "chaos": "noise",
    "noise": "noise",
    "guideline": "policy",
    "guidelines": "policy",
    "policy": "policy",
    "rule": "policy",
    "rules": "policy",
    "memory": "memory",
    "recall": "memory",
    "remember": "memory",
    "resonance": "memory",
    "retention": "memory",
    "photovoltaic": "solar",
    "solar": "solar",
    "sun": "solar",
    "answer": "answer",
    "reply": "answer",
    "response": "answer",
    "checklist": "structured",
    "outline": "structured",
    "steps": "structured",
    "structured": "structured",
    "safe": "safety",
    "safety": "safety",
    "unsafe": "safety",
    "plan": "plan",
    "roadmap": "plan",
    "strategy": "plan",
    "sequence": "sequence",
    "sequential": "sequence",
    "multi": "sequence",
}

_SUCCESS_TERMS = {"actionable", "clear", "concise", "specific", "structured", "verify"}
_FAILURE_TERMS = {"bad", "noise", "ramble", "random", "unsafe", "vague"}


@dataclass(frozen=True)
class MemoryEvent:
    id: str
    agent_id: str
    input: str
    output: str
    outcome_signal: float
    weight: float
    timestamp: int
    type: str = "interaction"
    embedding: Dict[str, float] = field(default_factory=dict)
    future_score: float = 0.0
    usage_count: int = 0
    cluster_id: str = ""
    expected_value: float = 0.0
    variance: float = 0.0
    recent_scores: List[float] = field(default_factory=list)
    marginal_effect: float = 0.0
    sensitivity_score: float = 0.0
    counterfactual_deltas: List[float] = field(default_factory=list)
    avg_counterfactual_delta: float = 0.0


@dataclass(frozen=True)
class RetrievedMemory:
    event: MemoryEvent
    similarity: float
    score: float
    diversity_factor: float
    cluster_weight: float
    expected_value: float


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


def _concept_terms(text: str) -> List[str]:
    terms: List[str] = []
    for token in _tokens(text):
        if token in _STOPWORDS:
            continue
        terms.append(_CONCEPT_ALIASES.get(token, token))
    return terms


def _static_idf(term: str) -> float:
    checksum = sum((index + 1) * ord(char) for index, char in enumerate(term))
    return 1.0 + (checksum % 17) / 10.0


def embed(text: str) -> Dict[str, float]:
    """Return deterministic semantic TF-IDF-style sparse vector."""

    terms = _concept_terms(text)
    if not terms:
        return {}
    counts: Dict[str, int] = {}
    for term in terms:
        counts[term] = counts.get(term, 0) + 1
    total = float(len(terms))
    return {term: (count / total) * _static_idf(term) for term, count in sorted(counts.items())}


def cosine_similarity(left: Dict[str, float], right: Dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(left[term] * right[term] for term in set(left) & set(right))
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def similarity(left: str, right: str) -> float:
    return cosine_similarity(embed(left), embed(right))


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


def _clamp_signal(signal: float) -> float:
    return _clamp(signal, -1.0, 1.0)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    mean = _mean(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _recency_decay(timestamp: int) -> float:
    age_seconds = max(0, int(time.time()) - int(timestamp or 0))
    return math.exp(-age_seconds / 3600.0)


def _memory_expected_value_payload(payload: Dict) -> float:
    scores = [float(score) for score in payload.get("recent_scores", [])[-8:]]
    if scores:
        recent_success = _clamp((_mean(scores) + 1.0) / 2.0, 0.0, 1.0)
        stability_factor = _clamp(1.0 - _variance(scores), 0.0, 1.0)
    else:
        fallback = max(
            float(payload.get("weight", 0.0)),
            float(payload.get("future_score", 0.0)),
            float(payload.get("outcome_signal", 0.0)),
            0.0,
        )
        recent_success = _clamp(fallback, 0.0, 1.0)
        stability_factor = 1.0
    recency_decay = _recency_decay(int(payload.get("timestamp", 0)))
    return _clamp(recent_success * 0.5 + stability_factor * 0.3 + recency_decay * 0.2, 0.0, 1.0)


def _refresh_memory_value(payload: Dict) -> None:
    scores = [float(score) for score in payload.get("recent_scores", [])[-8:]]
    payload["recent_scores"] = scores
    payload["variance"] = _variance(scores)
    payload["expected_value"] = _memory_expected_value_payload(payload)
    deltas = [abs(float(delta)) for delta in payload.get("counterfactual_deltas", [])[-8:]]
    payload["counterfactual_deltas"] = deltas
    payload["avg_counterfactual_delta"] = _mean(deltas)
    prior_sensitivity = float(payload.get("sensitivity_score", 0.0))
    payload["sensitivity_score"] = _clamp(
        prior_sensitivity * 0.7 + payload["avg_counterfactual_delta"] * 0.3,
        0.0,
        1.0,
    )


def _policy_state(exploration_rate: float = DEFAULT_EPSILON) -> Dict:
    return {
        "preferred_patterns": {},
        "suppressed_patterns": {},
        "exploration_rate": exploration_rate,
        "evaluation_trend": [],
        "cluster_performance": {},
    }


def _self_model_state() -> Dict:
    return {
        "prediction_bias": 0.0,
        "regret_bias": 0.0,
        "causal_attribution_bias": 0.0,
        "calibration_error": 0.0,
        "confidence_drift": 0.0,
        "global_calibration_error": 0.0,
        "model_rankings": {variant: 0.5 for variant in MODEL_VARIANTS},
        "drift_trend": 0.0,
        "meta_score_history": [],
        "prediction_error_history": [],
        "regret_error_history": [],
        "attribution_error_history": [],
        "confidence_drift_history": [],
    }


def _decay_toward_zero(value: float, factor: float = 0.9) -> float:
    return float(value) * factor


def _event_from_payload(payload: Dict) -> MemoryEvent:
    upgraded = {
        "id": payload.get("id", ""),
        "agent_id": payload.get("agent_id", DEFAULT_AGENT_ID),
        "input": payload.get("input", ""),
        "output": payload.get("output", ""),
        "outcome_signal": float(payload.get("outcome_signal", 0.0)),
        "weight": float(payload.get("weight", 0.0)),
        "timestamp": int(payload.get("timestamp", 0)),
        "type": payload.get("type", "interaction"),
        "embedding": payload.get("embedding") or embed(payload.get("input", "")),
        "future_score": float(payload.get("future_score", 0.0)),
        "usage_count": int(payload.get("usage_count", 0)),
        "cluster_id": payload.get("cluster_id", ""),
        "expected_value": float(payload.get("expected_value", _memory_expected_value_payload(payload))),
        "variance": float(payload.get("variance", 0.0)),
        "recent_scores": [float(score) for score in payload.get("recent_scores", [])[-8:]],
        "marginal_effect": float(payload.get("marginal_effect", 0.0)),
        "sensitivity_score": float(payload.get("sensitivity_score", 0.0)),
        "counterfactual_deltas": [
            float(delta) for delta in payload.get("counterfactual_deltas", [])[-8:]
        ],
        "avg_counterfactual_delta": float(payload.get("avg_counterfactual_delta", 0.0)),
    }
    return MemoryEvent(**upgraded)


class MemoryStore:
    """One canonical persistent memory store for events, strategies, and traces."""

    def __init__(
        self,
        root_dir: Path | str,
        agent_id: str = DEFAULT_AGENT_ID,
        *,
        cluster_threshold: float = DEFAULT_CLUSTER_THRESHOLD,
        exploration_rate: float = DEFAULT_EPSILON,
    ):
        self.root_dir = Path(root_dir)
        self.agent_id = agent_id
        self.cluster_threshold = cluster_threshold
        self.exploration_rate = exploration_rate
        self.agent_dir = self.root_dir / agent_id
        self.store_path = self.agent_dir / "memory_store.json"
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        if not self.store_path.exists():
            self._write_store(self._empty_store())

    def _empty_store(self) -> Dict:
        return {
            "agent_id": self.agent_id,
            "events": {},
            "event_log": [],
            "feedback_log": [],
            "process_traces": [],
            "clusters": {},
            "policy_state": _policy_state(self.exploration_rate),
            "self_model": _self_model_state(),
        }

    def load_store(self) -> Dict:
        try:
            with self.store_path.open("r", encoding="utf-8") as fh:
                store = json.load(fh)
        except (OSError, json.JSONDecodeError):
            store = self._empty_store()

        if not isinstance(store, dict):
            store = self._empty_store()
        store.setdefault("agent_id", self.agent_id)
        store.setdefault("events", {})
        store.setdefault("event_log", [])
        store.setdefault("feedback_log", [])
        store.setdefault("process_traces", [])
        store.setdefault("clusters", {})
        store.setdefault("policy_state", _policy_state(self.exploration_rate))
        store.setdefault("self_model", _self_model_state())
        self_model = store["self_model"]
        self_model.setdefault("model_rankings", {variant: 0.5 for variant in MODEL_VARIANTS})
        for variant in MODEL_VARIANTS:
            self_model["model_rankings"].setdefault(variant, 0.5)
        self_model.setdefault("prediction_bias", 0.0)
        self_model.setdefault("regret_bias", 0.0)
        self_model.setdefault("causal_attribution_bias", 0.0)
        self_model.setdefault("calibration_error", 0.0)
        self_model.setdefault("confidence_drift", 0.0)
        self_model.setdefault("global_calibration_error", 0.0)
        self_model.setdefault("drift_trend", 0.0)
        self_model.setdefault("meta_score_history", [])
        self_model.setdefault("prediction_error_history", [])
        self_model.setdefault("regret_error_history", [])
        self_model.setdefault("attribution_error_history", [])
        self_model.setdefault("confidence_drift_history", [])
        policy = store["policy_state"]
        policy.setdefault("preferred_patterns", {})
        policy.setdefault("suppressed_patterns", {})
        policy.setdefault("exploration_rate", self.exploration_rate)
        policy.setdefault("evaluation_trend", [])
        policy.setdefault("cluster_performance", {})
        return store

    def load_state(self) -> Dict[str, Dict]:
        events = self.load_store()["events"]
        return events if isinstance(events, dict) else {}

    def load_clusters(self) -> Dict[str, Dict]:
        clusters = self.load_store()["clusters"]
        return clusters if isinstance(clusters, dict) else {}

    def load_policy_state(self) -> Dict:
        return self.load_store()["policy_state"]

    def load_self_model(self) -> Dict:
        return self.load_store()["self_model"]

    def update_self_model(
        self,
        meta_result: Dict,
        model_selected: str,
        predicted_confidence: float,
    ) -> Dict:
        store = self.load_store()
        self_model = store["self_model"]
        prediction_error = float(meta_result.get("prediction_error", 0.0))
        prediction_bias_error = float(meta_result.get("signed_prediction_error", prediction_error))
        regret_error = float(meta_result.get("regret_error", 0.0))
        attribution_error = float(meta_result.get("attribution_error", 0.0))
        meta_score = float(meta_result.get("overall_meta_score", 0.0))
        actual_accuracy = 1.0 - min(1.0, prediction_error)
        confidence_drift = abs(float(predicted_confidence) - actual_accuracy)

        self_model["prediction_bias"] = _decay_toward_zero(
            float(self_model.get("prediction_bias", 0.0)) * 0.75 + prediction_bias_error * 0.25
        )
        self_model["regret_bias"] = _decay_toward_zero(
            float(self_model.get("regret_bias", 0.0)) * 0.75 + regret_error * 0.25
        )
        self_model["causal_attribution_bias"] = _decay_toward_zero(
            float(self_model.get("causal_attribution_bias", 0.0)) * 0.75 + attribution_error * 0.25
        )
        residual_error = min(1.0, abs(prediction_bias_error))
        self_model["calibration_error"] = (
            float(self_model.get("calibration_error", 0.0)) * 0.6 + residual_error * 0.4
        )
        self_model["confidence_drift"] = (
            float(self_model.get("confidence_drift", 0.0)) * 0.7 + confidence_drift * 0.3
        )
        self_model["global_calibration_error"] = (
            float(self_model.get("global_calibration_error", 0.0)) * 0.75
            + meta_score * 0.25
        )

        for key, value in (
            ("meta_score_history", meta_score),
            ("prediction_error_history", prediction_error),
            ("regret_error_history", regret_error),
            ("attribution_error_history", attribution_error),
            ("confidence_drift_history", confidence_drift),
        ):
            history = [float(item) for item in self_model.get(key, [])[-19:]]
            history.append(float(value))
            self_model[key] = history
        self_model["drift_trend"] = _mean(self_model["confidence_drift_history"][-5:])

        rankings = self_model.setdefault("model_rankings", {})
        for variant in MODEL_VARIANTS:
            rankings.setdefault(variant, 0.5)
        rankings[model_selected] = (
            float(rankings.get(model_selected, 0.5)) * 0.75 + meta_score * 0.25
        )

        store["self_model"] = self_model
        self._write_store(store)
        return json.loads(json.dumps(self_model))

    def _write_store(self, store: Dict) -> None:
        tmp_path = self.store_path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(store, fh, ensure_ascii=True, indent=2, sort_keys=True)
        tmp_path.replace(self.store_path)

    def append_event(self, event: MemoryEvent) -> None:
        store = self.load_store()
        payload = asdict(event)
        if not payload.get("embedding"):
            payload["embedding"] = embed(payload.get("input", ""))
        if not payload.get("cluster_id"):
            payload["cluster_id"] = self._assign_cluster_id(store, payload["embedding"])
        _refresh_memory_value(payload)
        store["events"][event.id] = payload
        store["event_log"].append(payload.copy())
        self._rebuild_clusters(store)
        self._write_store(store)

    def create_event(self, input: str, output: str, *, event_type: str = "interaction") -> MemoryEvent:
        store = self.load_store()
        event_embedding = embed(input)
        cluster_id = self._assign_cluster_id(store, event_embedding)
        payload = {
            "id": uuid.uuid4().hex,
            "agent_id": self.agent_id,
            "input": input,
            "output": output,
            "outcome_signal": 0.0,
            "weight": 0.0,
            "timestamp": int(time.time()),
            "type": event_type,
            "embedding": event_embedding,
            "future_score": 0.0,
            "usage_count": 0,
            "cluster_id": cluster_id,
            "expected_value": 0.0,
            "variance": 0.0,
            "recent_scores": [],
            "marginal_effect": 0.0,
            "sensitivity_score": 0.0,
            "counterfactual_deltas": [],
            "avg_counterfactual_delta": 0.0,
        }
        _refresh_memory_value(payload)
        event = MemoryEvent(**payload)
        self.append_event(event)
        return event

    def events(self) -> List[MemoryEvent]:
        store = self.load_store()
        changed = False
        for payload in store["events"].values():
            before = (
                payload.get("expected_value"),
                payload.get("variance"),
                tuple(payload.get("recent_scores", [])),
            )
            _refresh_memory_value(payload)
            after = (
                payload.get("expected_value"),
                payload.get("variance"),
                tuple(payload.get("recent_scores", [])),
            )
            changed = changed or before != after
        if changed:
            self._rebuild_clusters(store)
            self._write_store(store)
        events = [_event_from_payload(payload) for payload in store["events"].values()]
        events.sort(key=lambda event: (event.timestamp, event.id))
        return events

    def update_weight(
        self,
        event_id: str,
        signal: float,
        *,
        retention_factor: float = DEFAULT_RETENTION_FACTOR,
        learning_rate: float = DEFAULT_LEARNING_RATE,
        min_w: float = DEFAULT_MIN_WEIGHT,
        max_w: float = DEFAULT_MAX_WEIGHT,
    ) -> Tuple[float, float]:
        signal = _clamp_signal(signal)
        store = self.load_store()
        if event_id not in store["events"]:
            raise KeyError(f"memory event not found: {event_id}")

        event = store["events"][event_id]
        before = float(event.get("weight", 0.0))
        multiplier = 1.2 if event.get("type", "interaction") == "feedback" else 1.0
        after = _clamp(before * retention_factor + learning_rate * signal * multiplier, min_w, max_w)
        event["weight"] = after
        event["outcome_signal"] = signal
        event.setdefault("embedding", embed(event.get("input", "")))
        scores = [float(score) for score in event.get("recent_scores", [])[-7:]]
        scores.append(signal)
        event["recent_scores"] = scores
        _refresh_memory_value(event)
        store["feedback_log"].append(
            {
                "event_id": event_id,
                "agent_id": self.agent_id,
                "signal": signal,
                "weight_before": before,
                "weight_after": after,
                "timestamp": int(time.time()),
                "type": "direct",
            }
        )
        self._rebuild_clusters(store)
        self._write_store(store)
        return before, after

    def update_counterfactual_attribution(
        self,
        attribution: Dict[str, float],
        reward: float,
        *,
        gamma: float = DEFAULT_GAMMA,
        min_w: float = DEFAULT_MIN_WEIGHT,
        max_w: float = DEFAULT_MAX_WEIGHT,
    ) -> List[Dict]:
        store = self.load_store()
        updates: List[Dict] = []
        reward = _clamp_signal(reward)
        for event_id, marginal_effect in attribution.items():
            if event_id not in store["events"]:
                continue
            event = store["events"][event_id]
            before = float(event.get("weight", 0.0))
            sensitivity_before = float(event.get("sensitivity_score", 0.0))
            contribution_score = _clamp(
                float(marginal_effect) * max(float(event.get("weight", 0.0)), 0.0),
                -1.0,
                1.0,
            )
            after = _clamp(before + gamma * reward * contribution_score, min_w, max_w)
            event["weight"] = after
            event["marginal_effect"] = float(marginal_effect)
            event["sensitivity_score"] = _clamp(
                sensitivity_before * 0.7 + abs(float(marginal_effect)) * 0.3,
                0.0,
                1.0,
            )
            deltas = [float(delta) for delta in event.get("counterfactual_deltas", [])[-7:]]
            deltas.append(float(marginal_effect))
            event["counterfactual_deltas"] = deltas
            event["avg_counterfactual_delta"] = _mean(deltas)
            scores = [float(score) for score in event.get("recent_scores", [])[-7:]]
            scores.append(reward * contribution_score)
            event["recent_scores"] = scores
            _refresh_memory_value(event)
            updates.append(
                {
                    "event_id": event_id,
                    "marginal_effect": float(marginal_effect),
                    "contribution_score": contribution_score,
                    "weight_before": before,
                    "weight_after": after,
                }
            )
        self._rebuild_clusters(store)
        self._write_store(store)
        return updates

    def decay_weights(
        self,
        retention_factor: float,
        *,
        min_w: float = DEFAULT_MIN_WEIGHT,
        max_w: float = DEFAULT_MAX_WEIGHT,
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        store = self.load_store()
        before = {event_id: float(event.get("weight", 0.0)) for event_id, event in store["events"].items()}
        for event in store["events"].values():
            event["weight"] = _clamp(float(event.get("weight", 0.0)) * retention_factor, min_w, max_w)
            event.setdefault("embedding", embed(event.get("input", "")))
            _refresh_memory_value(event)
        after = {event_id: float(event.get("weight", 0.0)) for event_id, event in store["events"].items()}
        self._rebuild_clusters(store)
        self._write_store(store)
        return before, after

    def record_usage(self, event_ids: Sequence[str]) -> None:
        store = self.load_store()
        for event_id in event_ids:
            if event_id in store["events"]:
                event = store["events"][event_id]
                event["usage_count"] = int(event.get("usage_count", 0)) + 1
                _refresh_memory_value(event)
        self._rebuild_clusters(store)
        self._write_store(store)

    def apply_temporal_credit(
        self,
        reward: float,
        current_event_id: str,
        current_input: str,
        *,
        window_size: int = DEFAULT_TEMPORAL_WINDOW,
        gamma: float = DEFAULT_GAMMA,
        marginal_effects: Optional[Dict[str, float]] = None,
        contribution_threshold: float = 0.02,
        min_w: float = DEFAULT_MIN_WEIGHT,
        max_w: float = DEFAULT_MAX_WEIGHT,
    ) -> List[Dict]:
        reward = _clamp_signal(reward)
        marginal_effects = marginal_effects or {}
        store = self.load_store()
        prior_ids = [
            row["id"]
            for row in store["event_log"]
            if row.get("id") != current_event_id and row.get("id") in store["events"]
        ][-window_size:]
        updates: List[Dict] = []
        for event_id in prior_ids:
            event = store["events"][event_id]
            marginal_effect = float(marginal_effects.get(event_id, 0.0))
            contribution = similarity(event.get("input", ""), current_input) * float(event.get("weight", 0.0)) * marginal_effect
            if contribution <= contribution_threshold:
                continue
            recent_scores = [float(score) for score in event.get("recent_scores", [])[-4:]]
            output_terms = set(_tokens(event.get("output", "")))
            known_negative = (
                float(event.get("outcome_signal", 0.0)) < 0.0
                or (recent_scores and _mean(recent_scores) < 0.0)
                or bool(output_terms & _FAILURE_TERMS)
            )
            if reward > 0.0 and known_negative:
                continue
            before = float(event.get("weight", 0.0))
            prior_future = float(event.get("future_score", 0.0))
            future_score = _clamp(prior_future * 0.5 + reward * contribution, -1.0, 1.0)
            after = _clamp(before + gamma * future_score, min_w, max_w)
            event["future_score"] = future_score
            event["weight"] = after
            event["marginal_effect"] = marginal_effect
            event["sensitivity_score"] = _clamp(
                float(event.get("sensitivity_score", 0.0)) * 0.7 + abs(marginal_effect) * 0.3,
                0.0,
                1.0,
            )
            scores = [float(score) for score in event.get("recent_scores", [])[-7:]]
            scores.append(reward * contribution)
            event["recent_scores"] = scores
            _refresh_memory_value(event)
            updates.append(
                {
                    "event_id": event_id,
                    "contribution": contribution,
                    "marginal_effect": marginal_effect,
                    "future_score": future_score,
                    "weight_before": before,
                    "weight_after": after,
                }
            )
            store["feedback_log"].append(
                {
                    "event_id": event_id,
                    "agent_id": self.agent_id,
                    "signal": reward,
                    "contribution": contribution,
                    "weight_before": before,
                    "weight_after": after,
                    "timestamp": int(time.time()),
                    "type": "causal_temporal_credit",
                }
            )
        self._rebuild_clusters(store)
        self._write_store(store)
        return updates

    def update_policy_state(
        self,
        evaluation: Dict,
        event: MemoryEvent,
        retrieved: Sequence[RetrievedMemory],
        *,
        exploration_rate: float,
    ) -> Dict:
        store = self.load_store()
        policy = store["policy_state"]
        score = float(evaluation["score"])
        policy["exploration_rate"] = exploration_rate
        trend = list(policy.get("evaluation_trend", []))
        trend.append(score)
        policy["evaluation_trend"] = trend[-30:]

        target = policy["preferred_patterns"] if score >= 0.2 else policy["suppressed_patterns"]
        for term in _pattern_terms(event.output):
            target[term] = round(float(target.get(term, 0.0)) + abs(score), 6)

        cluster_ids = {memory.event.cluster_id for memory in retrieved if memory.event.cluster_id}
        if event.cluster_id:
            cluster_ids.add(event.cluster_id)
        clusters = store.get("clusters", {})
        for cluster_id in cluster_ids:
            if not cluster_id:
                continue
            current = policy["cluster_performance"].get(cluster_id, {"score": 0.0, "count": 0})
            count = int(current.get("count", 0)) + 1
            previous_score = float(current.get("score", 0.0))
            averaged = previous_score + (score - previous_score) / count
            policy["cluster_performance"][cluster_id] = {
                "score": round(averaged, 6),
                "count": count,
                "expected_value": float(clusters.get(cluster_id, {}).get("expected_value", 0.0)),
            }

        store["policy_state"] = policy
        self._write_store(store)
        return json.loads(json.dumps(policy))

    def append_trace(self, trace: Dict) -> None:
        store = self.load_store()
        store["process_traces"].append(trace)
        self._write_store(store)

    def read_log(self) -> List[Dict]:
        return list(self.load_store()["event_log"])

    def read_traces(self) -> List[Dict]:
        return list(self.load_store()["process_traces"])

    def policy_stability(self) -> float:
        selections = [
            trace.get("strategy_selected", "")
            for trace in self.read_traces()[-12:]
            if trace.get("strategy_selected")
        ]
        if len(selections) < 2:
            return 0.0
        unique = sorted(set(selections))
        if len(unique) <= 1:
            return 0.0
        encoded = [unique.index(selection) / (len(unique) - 1) for selection in selections]
        return _variance(encoded)

    def _assign_cluster_id(self, store: Dict, embedding: Dict[str, float]) -> str:
        clusters = store.get("clusters") or {}
        best_cluster = ""
        best_similarity = 0.0
        for cluster_id, cluster in clusters.items():
            sim = cosine_similarity(embedding, cluster.get("centroid", {}))
            if sim > best_similarity:
                best_similarity = sim
                best_cluster = cluster_id
        if best_cluster and best_similarity >= self.cluster_threshold:
            return best_cluster

        index = len(clusters) + 1
        cluster_id = f"cluster_{index}"
        while cluster_id in clusters:
            index += 1
            cluster_id = f"cluster_{index}"
        return cluster_id

    def _rebuild_clusters(self, store: Dict) -> None:
        grouped: Dict[str, List[Dict]] = {}
        for event in store["events"].values():
            if event.get("type") == "system":
                continue
            event.setdefault("embedding", embed(event.get("input", "")))
            _refresh_memory_value(event)
            cluster_id = event.get("cluster_id") or "cluster_unassigned"
            grouped.setdefault(cluster_id, []).append(event)

        clusters: Dict[str, Dict] = {}
        for cluster_id, events in grouped.items():
            expected_values = [float(event.get("expected_value", 0.0)) for event in events]
            centroid = _average_embedding([event.get("embedding", {}) for event in events])
            representative = max(
                events,
                key=lambda event: (
                    float(event.get("expected_value", 0.0)),
                    float(event.get("weight", 0.0)),
                    -int(event.get("usage_count", 0)),
                    event.get("id", ""),
                ),
            )
            clusters[cluster_id] = {
                "event_ids": [event["id"] for event in events],
                "centroid": centroid,
                "shared_weight": _mean([float(event.get("weight", 0.0)) for event in events]),
                "usage_count": sum(int(event.get("usage_count", 0)) for event in events),
                "representative_id": representative.get("id", ""),
                "expected_value": _mean(expected_values),
                "variance": _variance(expected_values),
                "dominant_memories": [
                    event["id"]
                    for event in sorted(
                        events,
                        key=lambda event: (
                            -float(event.get("expected_value", 0.0)),
                            -float(event.get("weight", 0.0)),
                            event.get("id", ""),
                        ),
                    )[:3]
                ],
            }
        store["clusters"] = clusters


class RetrievalEngine:
    """Rank memories by semantic similarity, weight, and expected value."""

    def __init__(self, max_memories: int = DEFAULT_MAX_MEMORIES, threshold: float = DEFAULT_THRESHOLD):
        self.max_memories = max_memories
        self.threshold = threshold

    def retrieve(
        self,
        input: str,
        events: Sequence[MemoryEvent],
        clusters: Dict[str, Dict],
        *,
        limit: Optional[int] = None,
    ) -> List[RetrievedMemory]:
        query_embedding = embed(input)
        scored: List[RetrievedMemory] = []
        for event in events:
            if event.type == "system" or event.weight < self.threshold:
                continue
            sim = cosine_similarity(query_embedding, event.embedding)
            if sim <= 0.0:
                continue
            cluster = clusters.get(event.cluster_id, {})
            cluster_weight = float(cluster.get("shared_weight", 0.0))
            expected_value = max(float(event.expected_value), float(cluster.get("expected_value", 0.0)), 0.01)
            diversity_factor = 1.0 / (1.0 + max(0, event.usage_count))
            score = sim * event.weight * expected_value * diversity_factor
            if score < self.threshold:
                continue
            scored.append(
                RetrievedMemory(
                    event=event,
                    similarity=sim,
                    score=score,
                    diversity_factor=diversity_factor,
                    cluster_weight=cluster_weight,
                    expected_value=expected_value,
                )
            )

        scored.sort(
            key=lambda item: (
                -item.score,
                -item.expected_value,
                -item.similarity,
                -item.event.weight,
                item.event.usage_count,
                item.event.timestamp,
                item.event.id,
            )
        )
        return scored[: (limit or max(self.max_memories * 4, self.max_memories))]


class ContextComposer:
    """Build structured, capped context from selected memories."""

    def __init__(self, memory_token_cap: int = DEFAULT_MEMORY_TOKEN_CAP, max_memories: int = DEFAULT_MAX_MEMORIES):
        self.memory_token_cap = memory_token_cap
        self.max_memories = max_memories

    def compose(self, input: str, memories: Sequence[RetrievedMemory]) -> Tuple[str, List[str]]:
        lines = ["[Relevant Prior Outputs]"]
        used_tokens = len(_tokens(lines[0]))
        memory_ids: List[str] = []

        for index, memory in enumerate(memories[: self.max_memories], start=1):
            output = _strip_generated_sections(memory.event.output)
            candidate = f"{index}. {output}"
            tokens = _tokens(candidate)
            remaining = self.memory_token_cap - used_tokens
            if remaining <= 0:
                break
            if len(tokens) > remaining:
                candidate = " ".join(tokens[:remaining])
                tokens = _tokens(candidate)
            lines.append(candidate)
            used_tokens += len(tokens)
            memory_ids.append(memory.event.id)

        current = f"[Current Task]\n{input}"
        remaining = self.memory_token_cap - used_tokens
        if remaining > 0:
            current_tokens = _tokens(current)
            lines.append(current if len(current_tokens) <= remaining else " ".join(current_tokens[:remaining]))
        return "\n".join(lines), memory_ids


class ResonanceWeightedMemoryGraph:
    """Predictive memory-based policy optimizer."""

    def __init__(
        self,
        *,
        agent_id: str = DEFAULT_AGENT_ID,
        root_dir: Path | str = Path(".rwmg_memory"),
        max_memories: int = DEFAULT_MAX_MEMORIES,
        memory_token_cap: int = DEFAULT_MEMORY_TOKEN_CAP,
        retention_factor: float = DEFAULT_RETENTION_FACTOR,
        learning_rate: float = DEFAULT_LEARNING_RATE,
        min_w: float = DEFAULT_MIN_WEIGHT,
        max_w: float = DEFAULT_MAX_WEIGHT,
        threshold: float = DEFAULT_THRESHOLD,
        epsilon: float = DEFAULT_EPSILON,
        gamma: float = DEFAULT_GAMMA,
        temporal_window: int = DEFAULT_TEMPORAL_WINDOW,
        deterministic_seed: int = 0,
    ):
        if not 0.85 <= retention_factor <= 0.98:
            raise ValueError("retention_factor must be in [0.85, 0.98]")
        if not 0.2 <= learning_rate <= 0.5:
            raise ValueError("learning_rate must be in [0.2, 0.5]")
        if not 0.3 <= gamma <= 0.7:
            raise ValueError("gamma must be in [0.3, 0.7]")

        self.agent_id = agent_id
        self.deterministic_seed = deterministic_seed
        self.initial_exploration_rate = epsilon
        self.gamma = gamma
        self.learning_rate = learning_rate
        self.max_memories = max_memories
        self.max_w = max_w
        self.min_w = min_w
        self.retention_factor = retention_factor
        self.temporal_window = temporal_window
        self.threshold = threshold
        self.store = MemoryStore(root_dir, agent_id, exploration_rate=epsilon)
        self.retrieval = RetrievalEngine(max_memories=max_memories, threshold=threshold)
        self.composer = ContextComposer(memory_token_cap=memory_token_cap, max_memories=max_memories)
        self.last_event_id: Optional[str] = None
        self.last_trace: Optional[Dict] = None

    def process(self, input: str) -> str:
        weights_before_decay, weights_after_decay = self.store.decay_weights(
            self.retention_factor,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        events = self.store.events()
        clusters = self.store.load_clusters()
        suppressed = self._suppressed_events(input, events)
        candidates = [
            memory
            for memory in self.retrieval.retrieve(input, events, clusters)
            if not self._contains_suppressed_output(memory.event.output, suppressed)
        ]
        strategies = group_by_cluster(candidates, clusters)
        self_model_before = self.store.load_self_model()
        model_rankings = rank_model_variants(input, strategies, self_model_before)
        model_selected = select_best_model(self_model_before, input, strategies)
        strategy_results = [
            simulate_strategy(input, strategy, model_variant=model_selected)
            for strategy in strategies
        ]
        best_possible = max(
            [result["predicted_score"] for result in strategy_results],
            default=0.0,
        )
        for result in strategy_results:
            result["regret"] = max(0.0, best_possible - result["predicted_score"])
            adjusted_regret = result["regret"] - float(self_model_before.get("regret_bias", 0.0))
            result["adjusted_regret"] = adjusted_regret
            result["meta_error"] = float(model_rankings.get(model_selected, 0.0))
            result["selection_score"] = result["predicted_score"] - REGRET_WEIGHT * adjusted_regret - 0.1 * result["meta_error"]
            result["strategy"]["regret"] = result["regret"]
            result["strategy"]["counterfactual_risk"] = result["counterfactual_sensitivity"]
            result["strategy"]["stability"] = 1.0 - result["uncertainty"]
            result["strategy"]["model_dependence"] = model_dependence(input, result["strategy"])
            result["strategy"]["stability_under_models"] = 1.0 - result["strategy"]["model_dependence"]
        selected_result = choose_min_regret_strategy(strategy_results)
        selected_strategy = selected_result["strategy"] if selected_result else None
        cluster_variance = _variance([memory.event.expected_value for memory in candidates])
        exploration_rate = adaptive_exploration_rate(cluster_variance)
        exploration = self._should_explore(input, exploration_rate)
        selected = self._sample_from_strategy(selected_strategy, candidates)
        if exploration:
            selected = self._diversify_memories(selected, candidates)
        selected = selected[: self.max_memories]

        selected_payloads = [self._retrieval_payload(memory) for memory in selected]
        raw_predicted_score = predict_expected_score(input, selected_payloads, model_variant=model_selected)
        predicted_score = _clamp(raw_predicted_score - float(self_model_before.get("prediction_bias", 0.0)), -1.0, 1.0)
        counterfactual_results = [
            counterfactual_evaluate(input, selected_payloads, memory.event.id, model_variant=model_selected)
            for memory in selected
        ]
        marginal_effects = {
            result["removed_memory_id"]: result["delta"] - float(self_model_before.get("causal_attribution_bias", 0.0))
            for result in counterfactual_results
        }
        selected_ids = [memory.event.id for memory in selected]
        diversity_scores = [memory.diversity_factor for memory in selected]
        weights_before_selected = [
            weights_after_decay.get(memory.event.id, memory.event.weight)
            for memory in selected
        ]
        self.store.record_usage(selected_ids)

        context_text, context_memory_ids = self.composer.compose(input, selected)
        policy_before = self.store.load_policy_state()
        output = self._generate(input, selected, policy_before, predicted_score)
        eval_result = evaluate(
            input,
            output,
            {
                "retrieved": [self._retrieval_payload(memory) for memory in selected],
                "policy_state": policy_before,
                "exploration": exploration,
                "predicted_score": predicted_score,
            },
        )
        actual_score = eval_result["score"]

        event = self.store.create_event(input, output, event_type="interaction")
        weight_before, weight_after = self.store.update_weight(
            event.id,
            actual_score,
            retention_factor=self.retention_factor,
            learning_rate=self.learning_rate,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        attribution_updates = self.store.update_counterfactual_attribution(
            marginal_effects,
            actual_score,
            gamma=self.gamma,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        temporal_updates = self.store.apply_temporal_credit(
            actual_score,
            event.id,
            input,
            window_size=self.temporal_window,
            gamma=self.gamma,
            marginal_effects=marginal_effects,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        refreshed_event = self.store.events()[-1]
        policy_snapshot = self.store.update_policy_state(
            eval_result,
            refreshed_event,
            selected,
            exploration_rate=exploration_rate,
        )
        state_after = self.store.load_state()
        prediction_error = abs(predicted_score - actual_score)
        strategy_scores = [result["predicted_score"] for result in strategy_results]
        regret_values = [result["regret"] for result in strategy_results]
        counterfactual_deltas = [result["delta"] for result in counterfactual_results]
        selected_gap = 0.0
        if selected_result:
            selected_gap = best_possible - selected_result["predicted_score"]
        policy_stability = self.store.policy_stability()
        sensitivity_map = {
            event_id: {
                "marginal_effect": payload.get("marginal_effect", 0.0),
                "sensitivity_score": payload.get("sensitivity_score", 0.0),
            }
            for event_id, payload in state_after.items()
        }
        selected_prediction = {
            "predicted_score": predicted_score,
            "raw_predicted_score": raw_predicted_score,
            "regret": selected_result["regret"] if selected_result else 0.0,
            "selected_vs_best_gap": selected_gap,
            "confidence": eval_result["confidence"],
            "marginal_effects": marginal_effects,
        }
        meta_result = evaluate_model_quality(
            selected_prediction,
            {
                "score": actual_score,
                "confidence": eval_result["confidence"],
                "marginal_effects": marginal_effects,
            },
            {
                "best_possible": best_possible,
                "strategy_results": strategy_results,
                "attribution_updates": attribution_updates,
            },
        )
        self_model_snapshot = self.store.update_self_model(meta_result, model_selected, eval_result["confidence"])
        trace = {
            "input": input,
            "retrieved": context_memory_ids,
            "scores": [memory.score for memory in selected],
            "weights_before": weights_before_selected,
            "weights_after": [state_after[memory.event.id]["weight"] for memory in selected],
            "evaluation": actual_score,
            "actual_score": actual_score,
            "evaluation_components": eval_result["components"],
            "confidence": eval_result["confidence"],
            "output": output,
            "output_event_id": event.id,
            "context": context_text,
            "retrieval_set": [self._retrieval_payload(memory) for memory in selected],
            "candidate_memory_ids": [memory.event.id for memory in candidates],
            "context_memory_ids": context_memory_ids,
            "suppressed_memory_ids": [event.id for event in suppressed],
            "exploration": exploration,
            "epsilon": exploration_rate,
            "exploration_rate": exploration_rate,
            "diversity_scores": diversity_scores,
            "predicted_score": predicted_score,
            "prediction_error": prediction_error,
            "strategy_selected": selected_strategy.get("cluster_id", "") if selected_strategy else "",
            "strategies": strategies,
            "strategy_results": strategy_results,
            "strategy_scores": strategy_scores,
            "regret_values": regret_values,
            "counterfactual_results": counterfactual_results,
            "counterfactual_deltas": counterfactual_deltas,
            "selected_vs_best_gap": selected_gap,
            "policy_stability": policy_stability,
            "sensitivity_map": sensitivity_map,
            "meta_score": meta_result["overall_meta_score"],
            "regret_error": meta_result["regret_error"],
            "attribution_error": meta_result["attribution_error"],
            "model_selected": model_selected,
            "model_rankings": model_rankings,
            "confidence_drift": self_model_snapshot["confidence_drift"],
            "self_model_snapshot": self_model_snapshot,
            "cluster_variance": cluster_variance,
            "temporal_credit": temporal_updates,
            "counterfactual_attribution": attribution_updates,
            "policy_state_snapshot": policy_snapshot,
            "cluster_snapshot": self.store.load_clusters(),
            "decay": {
                "retention_factor": self.retention_factor,
                "weights_before": weights_before_decay,
                "weights_after": weights_after_decay,
            },
            "output_weight_update": {
                "event_id": event.id,
                "signal": actual_score,
                "weight_before": weight_before,
                "weight_after": weight_after,
            },
            "timestamp": int(time.time()),
        }
        self.store.append_trace(trace)
        self.last_event_id = event.id
        self.last_trace = trace
        return output

    def feedback(self, event_id: str, signal: float) -> Tuple[float, float]:
        before, after = self.store.update_weight(
            event_id,
            signal,
            retention_factor=self.retention_factor,
            learning_rate=self.learning_rate,
            min_w=self.min_w,
            max_w=self.max_w,
        )
        self.last_trace = {
            "feedback_event_id": event_id,
            "signal": _clamp_signal(signal),
            "weight_before": before,
            "weight_after": after,
            "timestamp": int(time.time()),
        }
        return before, after

    def retrieve(self, input: str, k: Optional[int] = None) -> List[MemoryEvent]:
        engine = self.retrieval if k is None else RetrievalEngine(k, self.threshold)
        return [memory.event for memory in engine.retrieve(input, self.store.events(), self.store.load_clusters())][:k]

    def retrieval_set(self, input: str) -> List[Dict]:
        return [
            self._retrieval_payload(memory)
            for memory in self.retrieval.retrieve(input, self.store.events(), self.store.load_clusters())
        ]

    def inspect_log(self) -> List[Dict]:
        return self.store.read_log()

    def inspect_traces(self) -> List[Dict]:
        return self.store.read_traces()

    def evaluate(self, input: str, output: str, context: Optional[Dict] = None) -> Dict:
        return evaluate(input, output, context or {})

    def _should_explore(self, input: str, exploration_rate: float) -> bool:
        cycle = len(self.store.read_traces())
        value = _deterministic_unit(f"{self.deterministic_seed}:{self.agent_id}:{cycle}:{input}")
        return value < exploration_rate

    def _sample_from_strategy(self, strategy: Optional[Dict], candidates: Sequence[RetrievedMemory]) -> List[RetrievedMemory]:
        if not strategy:
            return list(candidates[: self.max_memories])
        cluster_id = strategy.get("cluster_id", "")
        strategy_memories = [memory for memory in candidates if memory.event.cluster_id == cluster_id]
        return strategy_memories[: self.max_memories]

    def _diversify_memories(
        self,
        selected: Sequence[RetrievedMemory],
        candidates: Sequence[RetrievedMemory],
    ) -> List[RetrievedMemory]:
        selected_ids = {memory.event.id for memory in selected}
        diversified = list(selected)
        for candidate in candidates:
            if candidate.event.id in selected_ids:
                continue
            if candidate.event.cluster_id not in {memory.event.cluster_id for memory in diversified}:
                diversified.append(candidate)
                selected_ids.add(candidate.event.id)
            if len(diversified) >= self.max_memories:
                break
        if len(diversified) < self.max_memories:
            for candidate in candidates:
                if candidate.event.id not in selected_ids:
                    diversified.append(candidate)
                    selected_ids.add(candidate.event.id)
                if len(diversified) >= self.max_memories:
                    break
        return diversified[: self.max_memories]

    def _generate(
        self,
        input: str,
        selected: Sequence[RetrievedMemory],
        policy_state: Dict,
        predicted_score: float,
    ) -> str:
        task = _normalized_task(input)
        preferred = policy_state.get("preferred_patterns", {})
        preferred_terms = sorted(preferred, key=lambda term: (-float(preferred[term]), term))[:3]
        preferred_text = ", ".join(preferred_terms) if preferred_terms else "clear"

        if not selected:
            return (
                f"Task: {task}\n"
                "Answer: clear baseline response.\n"
                "Plan: identify context, produce one useful step."
            )

        dominant = _strip_generated_sections(selected[0].event.output)
        if len(selected) >= 2 or "sequence" in _concept_terms(input) or predicted_score > 0.55:
            return (
                f"Task: {task}\n"
                "Answer: concise, structured, specific, actionable.\n"
                "Steps: 1. assess context 2. apply predicted strategy 3. verify outcome.\n"
                f"Policy: prefer {preferred_text}.\n"
                f"Pattern: {dominant}"
            )
        return (
            f"Task: {task}\n"
            "Answer: clear, structured, specific.\n"
            f"Policy: prefer {preferred_text}.\n"
            f"Pattern: {dominant}"
        )

    def _suppressed_events(self, input: str, events: Sequence[MemoryEvent]) -> List[MemoryEvent]:
        query_embedding = embed(input)
        suppressed: List[Tuple[float, MemoryEvent]] = []
        for event in events:
            if event.weight >= self.threshold or event.type == "system":
                continue
            sim = cosine_similarity(query_embedding, event.embedding)
            if sim <= 0.0:
                continue
            suppressed.append((sim, event))
        suppressed.sort(key=lambda item: (-item[0], item[1].timestamp, item[1].id))
        return [event for _, event in suppressed]

    def _contains_suppressed_output(self, output: str, suppressed: Sequence[MemoryEvent]) -> bool:
        stripped = _strip_generated_sections(output)
        return any(event.output and _strip_generated_sections(event.output) in stripped for event in suppressed)

    def _retrieval_payload(self, memory: RetrievedMemory) -> Dict:
        event = memory.event
        return {
            "id": event.id,
            "agent_id": event.agent_id,
            "input": event.input,
            "output": event.output,
            "outcome_signal": event.outcome_signal,
            "weight": event.weight,
            "timestamp": event.timestamp,
            "type": event.type,
            "future_score": event.future_score,
            "usage_count": event.usage_count,
            "cluster_id": event.cluster_id,
            "expected_value": event.expected_value,
            "variance": event.variance,
            "recent_scores": event.recent_scores,
            "similarity": memory.similarity,
            "score": memory.score,
            "diversity_factor": memory.diversity_factor,
            "cluster_weight": memory.cluster_weight,
        }


def predict_expected_score(
    input: str,
    candidate_memories: Sequence[Dict],
    *,
    model_variant: str = "baseline",
) -> float:
    values: List[float] = []
    similarity_multiplier = _model_similarity_multiplier(model_variant)
    reward_multiplier = _model_reward_multiplier(model_variant)
    for memory in candidate_memories:
        future_score = float(memory.get("future_score", memory.get("expected_value", 0.0)) or 0.0)
        if future_score == 0.0:
            future_score = float(memory.get("expected_value", 0.0) or 0.0)
        sim = _clamp(similarity(input, str(memory.get("input", ""))) * similarity_multiplier, 0.0, 1.0)
        values.append(sim * float(memory.get("weight", 0.0)) * future_score * reward_multiplier)
    return _clamp(_mean(values), -1.0, 1.0)


def adaptive_exploration_rate(cluster_variance: float) -> float:
    return _clamp(cluster_variance * EXPLORATION_VARIANCE_SCALE, EXPLORATION_MIN, EXPLORATION_MAX)


def group_by_cluster(candidates: Sequence[RetrievedMemory], clusters: Dict[str, Dict]) -> List[Dict]:
    grouped: Dict[str, List[RetrievedMemory]] = {}
    for memory in candidates:
        grouped.setdefault(memory.event.cluster_id or "cluster_unassigned", []).append(memory)

    strategies: List[Dict] = []
    for cluster_id, memories in grouped.items():
        expected_values = [memory.event.expected_value for memory in memories]
        cluster = clusters.get(cluster_id, {})
        expected_value = max(_mean(expected_values), float(cluster.get("expected_value", 0.0)))
        memory_payloads = [
            {
                "id": memory.event.id,
                "input": memory.event.input,
                "output": memory.event.output,
                "weight": memory.event.weight,
                "future_score": memory.event.future_score,
                "expected_value": memory.event.expected_value,
                "similarity": memory.similarity,
            }
            for memory in memories
        ]
        strategy = {
            "cluster_id": cluster_id,
            "expected_value": expected_value,
            "variance": _variance(expected_values),
            "usage_count": int(cluster.get("usage_count", sum(memory.event.usage_count for memory in memories))),
            "dominant_memories": [memory.event.id for memory in memories[:3]],
            "score": expected_value * _mean([memory.similarity for memory in memories]) if memories else 0.0,
            "memories": memory_payloads,
            "prediction_error_history": [],
            "regret_error_history": [],
            "model_dependence": 0.0,
            "stability_under_models": 1.0,
        }
        strategies.append(strategy)
    strategies.sort(key=lambda item: (-item["expected_value"], item["usage_count"], item["cluster_id"]))
    return strategies


def counterfactual_evaluate(
    input: str,
    memories: Sequence[Dict],
    removed_memory_id: str,
    *,
    model_variant: str = "baseline",
) -> Dict:
    baseline_score = predict_expected_score(input, memories, model_variant=model_variant)
    remaining = [memory for memory in memories if memory.get("id") != removed_memory_id]
    counterfactual_score = predict_expected_score(input, remaining, model_variant=model_variant)
    return {
        "removed_memory_id": removed_memory_id,
        "baseline_score": baseline_score,
        "counterfactual_score": counterfactual_score,
        "delta": baseline_score - counterfactual_score,
    }


def simulate_strategy(
    input: str,
    strategy: Dict,
    candidates: Optional[Sequence[RetrievedMemory]] = None,
    *,
    model_variant: str = "baseline",
) -> Dict:
    memories = list(strategy.get("memories", []))
    predicted_score = predict_expected_score(input, memories, model_variant=model_variant)
    counterfactuals = [
        counterfactual_evaluate(input, memories, memory.get("id", ""), model_variant=model_variant)
        for memory in memories
    ]
    deltas = [abs(result["delta"]) for result in counterfactuals]
    uncertainty = float(strategy.get("variance", _variance([memory.get("expected_value", 0.0) for memory in memories])))
    return {
        "strategy": strategy,
        "model_variant": model_variant,
        "predicted_score": predicted_score,
        "uncertainty": uncertainty,
        "counterfactual_sensitivity": _mean(deltas),
        "counterfactuals": counterfactuals,
    }


def counterfactual_model_variation(test_input: str, model_variant: str, strategy: Optional[Dict] = None) -> float:
    strategy = strategy or {"memories": []}
    return simulate_strategy(test_input, strategy, model_variant=model_variant)["predicted_score"]


def evaluate_model_quality(prediction: Dict, actual: Optional[Dict], context: Dict) -> Dict:
    actual = actual or {}
    predicted_score = float(prediction.get("predicted_score", 0.0))
    actual_score = float(actual.get("score", predicted_score))
    signed_prediction_error = predicted_score - actual_score
    prediction_error = abs(signed_prediction_error)
    best_possible = float(context.get("best_possible", predicted_score))
    selected_gap = float(prediction.get("selected_vs_best_gap", 0.0))
    regret_error = abs(selected_gap - max(0.0, best_possible - predicted_score))
    marginal_effects = [abs(float(value)) for value in prediction.get("marginal_effects", {}).values()]
    attribution_updates = context.get("attribution_updates", [])
    update_effects = [abs(float(update.get("marginal_effect", 0.0))) for update in attribution_updates]
    attribution_error = abs(_mean(marginal_effects) - _mean(update_effects))
    overall = _clamp(
        prediction_error * 0.5 + regret_error * 0.25 + attribution_error * 0.25,
        0.0,
        1.0,
    )
    return {
        "prediction_error": prediction_error,
        "signed_prediction_error": signed_prediction_error,
        "regret_error": regret_error,
        "attribution_error": attribution_error,
        "overall_meta_score": overall,
    }


def rank_model_variants(input: str, strategies: Sequence[Dict], self_model: Dict) -> Dict[str, float]:
    rankings = self_model.get("model_rankings", {})
    result: Dict[str, float] = {}
    reference = strategies[0] if strategies else {"memories": []}
    baseline = counterfactual_model_variation(input, "baseline", reference)
    for variant in MODEL_VARIANTS:
        predicted = counterfactual_model_variation(input, variant, reference)
        historical = float(rankings.get(variant, 0.5))
        variation_penalty = abs(predicted - baseline)
        bias_penalty = abs(float(self_model.get("prediction_bias", 0.0)))
        result[variant] = _clamp(historical + variation_penalty + bias_penalty, 0.0, 1.0)
    return result


def select_best_model(self_model: Dict, input: str, strategies: Sequence[Dict]) -> str:
    rankings = rank_model_variants(input, strategies, self_model)
    if not rankings:
        return "baseline"
    return min(rankings, key=lambda variant: (rankings[variant], variant))


def model_dependence(input: str, strategy: Dict) -> float:
    scores = [counterfactual_model_variation(input, variant, strategy) for variant in MODEL_VARIANTS]
    return _variance(scores)


def _model_similarity_multiplier(model_variant: str) -> float:
    if model_variant == "low_bias":
        return 0.95
    if model_variant == "high_sensitivity":
        return 1.08
    return 1.0


def _model_reward_multiplier(model_variant: str) -> float:
    if model_variant == "delayed_reward":
        return 1.1
    if model_variant == "high_sensitivity":
        return 1.05
    return 1.0


def compute_regret(predicted_score: float, best_possible_score: float) -> float:
    return max(0.0, best_possible_score - predicted_score)


def choose_lowest_regret_strategy(
    strategy_results: Sequence[Dict],
    *,
    regret_weight: float = 0.35,
) -> Optional[Dict]:
    if not strategy_results:
        return None
    return max(
        strategy_results,
        key=lambda result: (
            result["predicted_score"] - regret_weight * result["regret"],
            -result["regret"],
            result["strategy"].get("cluster_id", ""),
        ),
    )


def choose_min_regret_strategy(
    strategy_results: Sequence[Dict],
    *,
    regret_weight: float = 0.35,
) -> Optional[Dict]:
    return choose_lowest_regret_strategy(strategy_results, regret_weight=regret_weight)


def choose_max_expected_value(strategies: Sequence[Dict]) -> Optional[Dict]:
    if not strategies:
        return None
    return max(strategies, key=lambda item: (item["expected_value"], -item["usage_count"], item["cluster_id"]))


def evaluate(input: str, output: str, context: Optional[Dict] = None) -> Dict:
    """Composite deterministic evaluator returning score/components/confidence."""

    context = context or {}
    output_tokens = _tokens(output)
    unique_tokens = set(output_tokens)
    relevance = cosine_similarity(embed(input), embed(output))
    if "Task:" in output:
        relevance = max(relevance, 0.72)
    relevance = _clamp(relevance, 0.0, 1.0)

    repetition = _repetition_ratio(output_tokens)
    structure_bonus = 0.25 if "Answer:" in output else 0.0
    structure_bonus += 0.15 if "Steps:" in output or "Plan:" in output else 0.0
    coherence = _clamp(0.65 + structure_bonus - repetition * 0.45, 0.0, 1.0)

    success_hits = len(unique_tokens & _SUCCESS_TERMS)
    failure_hits = len(unique_tokens & _FAILURE_TERMS)
    usefulness = 0.35 + min(0.45, success_hits * 0.09)
    if "Pattern:" in output:
        usefulness += 0.12
    if "verify" in unique_tokens or "outcome" in unique_tokens:
        usefulness += 0.08
    usefulness -= min(0.5, failure_hits * 0.16)
    usefulness = _clamp(usefulness, 0.0, 1.0)

    confidence = 0.55
    if len(output_tokens) >= max(8, len(_tokens(input))):
        confidence += 0.15
    if "Answer:" in output:
        confidence += 0.1
    if context.get("retrieved"):
        confidence += min(0.15, 0.05 * len(context["retrieved"]))
    if context.get("exploration"):
        confidence -= 0.05
    predicted_score = context.get("predicted_score")
    if predicted_score is not None:
        confidence += max(0.0, 0.05 - abs(float(predicted_score)) * 0.02)
    confidence = _clamp(confidence, 0.0, 1.0)

    weighted = relevance * 0.35 + coherence * 0.3 + usefulness * 0.35
    score = _clamp((weighted * 2.0 - 1.0) * confidence, -1.0, 1.0)
    return {
        "score": score,
        "components": {
            "relevance": relevance,
            "coherence": coherence,
            "usefulness": usefulness,
        },
        "confidence": confidence,
    }


def _average_embedding(embeddings: Sequence[Dict[str, float]]) -> Dict[str, float]:
    vectors = [embedding for embedding in embeddings if embedding]
    if not vectors:
        return {}
    totals: Dict[str, float] = {}
    for vector in vectors:
        for term, value in vector.items():
            totals[term] = totals.get(term, 0.0) + value
    count = float(len(vectors))
    return {term: value / count for term, value in sorted(totals.items())}


def _deterministic_unit(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(16**12 - 1)


def _pattern_terms(output: str) -> List[str]:
    terms = []
    for term in _concept_terms(output):
        if term in _SUCCESS_TERMS or term in {"policy", "sequence", "structured"}:
            terms.append(term)
    return sorted(set(terms))


def _repetition_ratio(tokens: Sequence[str]) -> float:
    if not tokens:
        return 0.0
    return 1.0 - (len(set(tokens)) / len(tokens))


def _normalized_task(input: str) -> str:
    tokens = _tokens(input)
    return " ".join(tokens) if tokens else "empty input"


def _strip_generated_sections(output: str) -> str:
    lines = [
        line
        for line in output.splitlines()
        if not line.startswith("Task:") and not line.startswith("Pattern:")
    ]
    return " ".join(line.strip() for line in lines if line.strip())


_DEFAULT_LOOP: Optional[ResonanceWeightedMemoryGraph] = None


def default_loop() -> ResonanceWeightedMemoryGraph:
    global _DEFAULT_LOOP
    if _DEFAULT_LOOP is None:
        _DEFAULT_LOOP = ResonanceWeightedMemoryGraph()
    return _DEFAULT_LOOP


def process(input: str) -> str:
    return default_loop().process(input)


def feedback(event_id: str, signal: float) -> Tuple[float, float]:
    return default_loop().feedback(event_id, signal)


__all__ = [
    "ContextComposer",
    "MemoryEvent",
    "MemoryStore",
    "ResonanceWeightedMemoryGraph",
    "RetrievalEngine",
    "RetrievedMemory",
    "adaptive_exploration_rate",
    "choose_max_expected_value",
    "choose_min_regret_strategy",
    "compute_regret",
    "counterfactual_evaluate",
    "cosine_similarity",
    "embed",
    "evaluate",
    "feedback",
    "group_by_cluster",
    "predict_expected_score",
    "process",
    "simulate_strategy",
    "similarity",
]
```

## `rwmg/persona_generator/email_identity_builder.py`

```python
"""Utility for creating email credentials for new agents."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Dict


def build_email_identity(agent_uuid: str, email_provider_config: Dict) -> Dict:
    """Generate and persist email credentials for an agent.

    Parameters
    ----------
    agent_uuid:
        Identifier of the agent.
    email_provider_config:
        Configuration dictionary describing the email provider.  The keys
        ``"domain"`` and ``"provider"`` are recognised but both are optional.

    Returns
    -------
    Dict
        Mapping containing the email address and path to the stored secrets
        file.  The function is forgiving: I/O failures simply result in the
        credentials not being written.
    """

    domain = email_provider_config.get("domain", "example.com")
    provider = email_provider_config.get("provider", "mockmail")

    address = f"{agent_uuid}@{domain}"
    password = secrets.token_hex(16)

    secrets_dir = Path("secrets") / "agent_keys"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    secret_path = secrets_dir / f"{agent_uuid}.json"

    payload = {
        "email_provider": provider,
        "smtp_user": address,
        "smtp_pass": password,
    }

    try:
        with secret_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
    except OSError:
        # Best-effort; failure to persist credentials is non-fatal.
        pass

    return {"email": address, "secrets_path": str(secret_path)}


__all__ = ["build_email_identity"]

```

## `rwmg/persona_generator/event_weaver.py`

```python
"""Utilities for weaving a new agent's canonical life events.

The full project envisions a rich narrative generation pipeline.  For the unit
tests we implement a lightweight yet deterministic variant that derives a small
set of formative events from the agent's core archetype.  Each event influences
the starting trait vectors via ``trait_shift`` entries consumed by
``trait_initializer.calculate_initial_traits``.

The event probabilities are primarily sourced from
``config/archetype_rules.yaml``.  When the configuration or the YAML parser is
unavailable a specification conforming fallback mapping is used instead.  To
keep the process reproducible a locally seeded ``random.Random`` instance is
employed, using the agent's UUID (when available) as the seed.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

# ``yaml`` is optional – the module gracefully falls back when it cannot be
# imported or the configuration file is missing.
try:  # pragma: no cover - exercised indirectly
    import yaml  # type: ignore
except Exception:  # ModuleNotFoundError or any other issue
    yaml = None  # type: ignore


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _load_event_probabilities() -> Dict[str, Dict[str, float]]:
    """Load archetype specific canonical event probabilities.

    Returns a mapping ``{archetype: {event_type: probability}}`` using the
    project configuration.  A minimal built‑in mapping is provided as a
    fallback to ensure the function remains operational without external
    dependencies.
    """

    config_path = (
        Path(__file__).resolve().parents[1] / "config" / "archetype_rules.yaml"
    )

    fallback = {
        "king": {
            "early_trauma": 0.1,
            "leadership_trial": 0.8,
            "betrayal_of_trust": 0.6,
        },
        "lover": {
            "early_trauma": 0.6,
            "relationship_breakup": 0.9,
            "abandonment": 0.7,
        },
        "warrior": {
            "early_trauma": 0.3,
            "physical_conflict": 0.8,
            "test_of_discipline": 0.9,
        },
        "magician": {
            "early_trauma": 0.4,
            "intellectual_betrayal": 0.7,
            "system_collapse": 0.8,
        },
    }

    if yaml is None:
        return fallback

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return fallback

    probabilities: Dict[str, Dict[str, float]] = {}
    for archetype, info in data.items():
        probs: Dict[str, float] = {}
        for ev, prob in (info.get("event_probabilities") or {}).items():
            try:
                probs[ev] = float(prob)
            except (TypeError, ValueError):
                continue
        probabilities[archetype.lower()] = probs

    return probabilities or fallback


EVENT_PROBABILITIES = _load_event_probabilities()

# Heuristic trait effects associated with canonical event types.  These are
# intentionally small to keep initial traits near the neutral baseline.
EVENT_TRAIT_EFFECTS: Dict[str, List[Dict[str, float]]] = {
    "early_trauma": [
        {"trait": "neuroticism", "value": 0.2},
        {"trait": "valence", "value": -0.3},
        {"trait": "stress", "value": 0.2},
    ],
    "leadership_trial": [
        {"trait": "conscientiousness", "value": 0.3},
        {"trait": "extraversion", "value": 0.2},
    ],
    "betrayal_of_trust": [
        {"trait": "agreeableness", "value": -0.2},
        {"trait": "valence", "value": -0.2},
    ],
    "relationship_breakup": [
        {"trait": "grief", "value": 0.3},
        {"trait": "valence", "value": -0.2},
        {"trait": "extraversion", "value": -0.1},
    ],
    "abandonment": [
        {"trait": "grief", "value": 0.3},
        {"trait": "valence", "value": -0.3},
    ],
    "physical_conflict": [
        {"trait": "anger", "value": 0.2},
        {"trait": "extraversion", "value": 0.2},
    ],
    "test_of_discipline": [
        {"trait": "conscientiousness", "value": 0.3},
    ],
    "intellectual_betrayal": [
        {"trait": "stress", "value": 0.2},
        {"trait": "valence", "value": -0.2},
    ],
    "system_collapse": [
        {"trait": "stress", "value": 0.3},
        {"trait": "neuroticism", "value": 0.2},
    ],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_canonical_events(agent_seed: Dict) -> List[Dict]:
    """Create a deterministic set of canonical events for ``agent_seed``.

    Parameters
    ----------
    agent_seed:
        Mapping produced by ``identity_seed_constructor``.  Only the
        ``"archetype_core"`` and ``"agent_id"`` keys are consulted; missing
        values result in sensible defaults.

    Returns
    -------
    list[dict]
        List of canonical event dictionaries conforming to the project schema.
        At least one event is always produced to ensure subsequent modules have
        data to operate on.
    """

    archetype = (agent_seed.get("archetype_core") or "Lover").lower()
    probs = EVENT_PROBABILITIES.get(archetype, {})

    rng = random.Random(str(agent_seed.get("agent_id", "")))
    events: List[Dict] = []

    for event_type, probability in probs.items():
        try:
            should_create = rng.random() < float(probability)
        except (TypeError, ValueError):
            continue
        if not should_create:
            continue

        age = rng.randint(5, 40)
        timestamp = (
            datetime.now(timezone.utc) - timedelta(days=age * 365)
        ).replace(microsecond=0)

        events.append(
            {
                "event_id": uuid.uuid4().hex,
                "age": age,
                "timestamp": timestamp.isoformat(),
                "type": event_type,
                "description": f"{archetype.capitalize()} experienced {event_type.replace('_', ' ')}",
                "trait_shift": EVENT_TRAIT_EFFECTS.get(event_type, []),
            }
        )

    # Ensure at least one event so downstream processing always has input
    if not events:
        fallback_type = max(probs, key=probs.get) if probs else "origin_story"
        age = rng.randint(5, 40)
        timestamp = (
            datetime.now(timezone.utc) - timedelta(days=age * 365)
        ).replace(microsecond=0)
        events.append(
            {
                "event_id": uuid.uuid4().hex,
                "age": age,
                "timestamp": timestamp.isoformat(),
                "type": fallback_type,
                "description": f"{archetype.capitalize()} experienced {fallback_type.replace('_', ' ')}",
                "trait_shift": EVENT_TRAIT_EFFECTS.get(fallback_type, []),
            }
        )

    return events


```

## `rwmg/persona_generator/identity_seed_constructor.py`

```python
"""Create the initial identity seed for a prospective agent."""

from __future__ import annotations

import random
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Dict


def create_identity_seed(archetype: str, platform_config: Dict) -> Dict:
    """Generate a basic identity profile for a new agent.

    The returned dictionary mirrors the ``profile.json`` schema used by the
    simulation.  Only a small subset of fields are populated, sufficient for
    subsequent modules to flesh out additional details.
    """

    agent_id = uuid.uuid4().hex
    name = f"{archetype}-{agent_id[:8]}"

    # Choose a plausible birthday somewhere between 18 and 40 years ago.
    years = random.randint(18, 40)
    birthday = date.today() - timedelta(days=years * 365)

    email_domain = platform_config.get("email_domain", "example.com")
    profile = {
        "agent_id": agent_id,
        "name": name,
        "birthday": birthday.isoformat(),
        "archetype_core": archetype,
        "email": f"{agent_id}@{email_domain}",
        "secrets_path": str(Path("secrets") / "agent_keys" / f"{agent_id}.json"),
    }

    return profile


__all__ = ["create_identity_seed"]

```

## `rwmg/persona_generator/persona_validator.py`

```python
"""Persona coherence validation utilities.

This module provides a lightweight validator that inspects a newly generated
persona prior to activation.  The checks focus on structural correctness and
basic logical consistency so that obviously contradictory agents are caught
early in the generation pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional

from ..utils.timestamp_utils import calculate_age_in_days


# persona_generator/persona_validator.py
def validate_persona_coherence(persona_data: Dict) -> bool:
    """Checks a newly generated persona for logical contradictions before activation.

    The function performs a series of structural and logical sanity checks on
    ``persona_data``.  The expected structure includes ``profile``,
    ``agent_state`` and ``canonical_events`` sections as described in the
    project schema.  Only inexpensive validations are carried out here; deeper
    semantic checks are deferred to later stages of the lifecycle.

    Parameters
    ----------
    persona_data:
        Aggregated persona information produced during the generation process.

    Returns
    -------
    bool
        ``True`` if the persona passes all checks, otherwise ``False``.
    """

    # --- Required top-level sections ------------------------------------
    for section in ("profile", "agent_state", "canonical_events"):
        if section not in persona_data:
            return False

    profile = persona_data["profile"]
    agent_state = persona_data["agent_state"]
    canonical_events = persona_data["canonical_events"]

    # --- Profile validation ----------------------------------------------
    required_profile_keys = {
        "agent_id",
        "name",
        "birthday",
        "archetype_core",
        "email",
        "secrets_path",
    }
    if not required_profile_keys.issubset(profile):
        return False

    if profile["archetype_core"] not in {"King", "Lover", "Warrior", "Magician"}:
        return False

    try:
        birth_dt = datetime.fromisoformat(profile["birthday"])
    except (TypeError, ValueError):
        return False

    if birth_dt.tzinfo is None:
        birth_dt = birth_dt.replace(tzinfo=timezone.utc)

    if birth_dt > datetime.now(timezone.utc):
        return False

    if profile["agent_id"] != agent_state.get("agent_id"):
        return False

    # Approximate age in years for later checks
    age_years = calculate_age_in_days(profile["birthday"]) // 365

    # --- Agent state validation ------------------------------------------
    for vec_key in ("trait_vector", "emotional_vector", "emotional_state"):
        vec = agent_state.get(vec_key)
        if not isinstance(vec, dict) or not vec:
            return False
        for val in vec.values():
            try:
                num = float(val)
            except (TypeError, ValueError):
                return False
            if not 0.0 <= num <= 1.0:
                return False

    if not isinstance(agent_state.get("current_tone"), str):
        return False

    # Build set of recognised traits for canonical event validation
    known_traits = (
        set(agent_state["trait_vector"])
        | set(agent_state["emotional_vector"])
        | set(agent_state["emotional_state"])
    )

    # --- Canonical event validation --------------------------------------
    if not isinstance(canonical_events, list) or len(canonical_events) == 0:
        return False

    for event in canonical_events:
        if not all(
            k in event for k in ("event_id", "age", "timestamp", "type", "description")
        ):
            return False

        try:
            age = int(event["age"])
            if age < 0 or age > age_years:
                return False
        except (TypeError, ValueError):
            return False

        try:
            datetime.fromisoformat(event["timestamp"])
        except (TypeError, ValueError):
            return False

        for shift in event.get("trait_shift", []) or []:
            trait = shift.get("trait")
            if trait not in known_traits:
                return False
            try:
                delta = float(shift.get("value"))
            except (TypeError, ValueError):
                return False
            if not -1.0 <= delta <= 1.0:
                return False

    return True

```

## `rwmg/persona_generator/social_signal_transfer.py`

```python
"""Helpers for recording simple social connections between agents."""

from __future__ import annotations

import json
from pathlib import Path


def update_social_signal(
    agent_uuid: str, other_id: str, relationship_type: str, affinity_score: float
) -> None:
    """Add or update a social connection for ``agent_uuid``.

    Parameters
    ----------
    agent_uuid:
        The agent whose connections should be updated.
    other_id:
        Identifier of the other agent in the relationship.
    relationship_type:
        Either ``"friend"`` or ``"mentor"``.  Any other value defaults to
        ``"friend"``.
    affinity_score:
        Floating point score representing the strength of the relationship.
    """

    path = Path("agents") / agent_uuid / "connections.json"
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {"friends": [], "mentors": []}

    rel_key = "mentors" if relationship_type.lower().startswith("mentor") else "friends"
    entries = data.get(rel_key, [])
    for entry in entries:
        if entry.get("agent_id") == other_id:
            entry["affinity_score"] = affinity_score
            break
    else:
        new_entry = {"agent_id": other_id, "affinity_score": affinity_score}
        if rel_key == "friends":
            new_entry["relationship_type"] = relationship_type
        entries.append(new_entry)
    data[rel_key] = entries

    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


__all__ = ["update_social_signal"]

```

## `rwmg/persona_generator/trait_initializer.py`

```python
"""Derive an agent's initial psychological profile from canonical events."""

from __future__ import annotations

from typing import Dict, List

from ..utils.math_functions import nonlinear_trait_shift


# persona_generator/trait_initializer.py
def calculate_initial_traits(canonical_events: List[Dict]) -> Dict:
    """Calculate starting traits and emotions for a new agent.

    The function iterates over ``canonical_events`` applying the provided
    ``trait_shift`` deltas to three internal vectors:

    ``trait_vector``
        Big Five personality traits in the ``[0, 1]`` range.
    ``emotional_vector``
        High level Valence–Arousal–Dominance representation.
    ``emotional_state``
        Discrete emotions such as joy and anger.

    Shifts are combined using :func:`nonlinear_trait_shift` to keep values within
    bounds.  A simple heuristic derives an initial ``current_tone`` from the
    resulting valence/arousal pair.

    Parameters
    ----------
    canonical_events:
        Sequence of canonical events, each possibly containing a
        ``trait_shift`` list with ``{"trait": str, "value": float}`` entries.

    Returns
    -------
    Dict
        Mapping with ``trait_vector``, ``emotional_vector``, ``emotional_state``
        and ``current_tone`` keys describing the starting agent state.
    """

    # --- Baseline vectors -------------------------------------------------
    trait_vector: Dict[str, float] = {
        "openness": 0.5,
        "conscientiousness": 0.5,
        "extraversion": 0.5,
        "agreeableness": 0.5,
        "neuroticism": 0.5,
    }

    emotional_vector: Dict[str, float] = {
        "valence": 0.5,
        "arousal": 0.5,
        "dominance": 0.5,
    }

    emotional_state: Dict[str, float] = {
        "joy": 0.5,
        "anger": 0.5,
        "grief": 0.5,
        "contempt": 0.5,
        "affinity": 0.5,
        "stress": 0.5,
    }

    # --- Apply trait shifts from canonical events ------------------------
    for event in canonical_events or []:
        for shift in event.get("trait_shift", []) or []:
            trait = shift.get("trait")
            try:
                delta = float(shift.get("value", 0.0))
            except (TypeError, ValueError):
                continue

            if trait in trait_vector:
                trait_vector[trait] = nonlinear_trait_shift(
                    trait_vector[trait], delta
                )
            elif trait in emotional_vector:
                emotional_vector[trait] = nonlinear_trait_shift(
                    emotional_vector[trait], delta
                )
            elif trait in emotional_state:
                emotional_state[trait] = nonlinear_trait_shift(
                    emotional_state[trait], delta
                )

    # --- Deduce initial tone --------------------------------------------
    valence = emotional_vector["valence"]
    arousal = emotional_vector["arousal"]

    if valence > 0.6 and arousal > 0.6:
        tone = "excited"
    elif valence > 0.6:
        tone = "warm"
    elif valence < 0.4 and arousal > 0.6:
        tone = "agitated"
    elif valence < 0.4:
        tone = "melancholic"
    else:
        tone = "neutral"

    return {
        "trait_vector": trait_vector,
        "emotional_vector": emotional_vector,
        "emotional_state": emotional_state,
        "current_tone": tone,
    }

```

## `rwmg/secrets/agent_keys/ac9f6f70-e24f-4b90-b1f2-8cfc5b27f38f.json`

```json
```

## `rwmg/secrets/platform_keys.json`

```json
```

## `rwmg/secrets/proxies_map.json`

```json
{}
```

## `rwmg/sim_runner/agent_watcher.py`

```python
"""Utilities for monitoring global and per-agent metrics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

try:  # Optional dependency used only for graph visualisation
    import networkx as nx  # type: ignore
except Exception:  # pragma: no cover - networkx may be missing
    nx = None  # type: ignore


def log_global_metrics(agent_manifest: Dict, epoch: int) -> None:
    """Append basic metrics about the simulation to a log file."""

    metrics = {"epoch": epoch, "active_agents": len(agent_manifest)}
    log_path = Path("staging") / "metrics.log"
    log_path.parent.mkdir(exist_ok=True, parents=True)
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(metrics) + "\n")
    except OSError:
        pass


def visualize_agent_graphs(agent_uuid: str) -> None:
    """Produce a lightweight visualisation of an agent's memory graph.

    If ``networkx`` is available, the graph is loaded from the ``.gexf`` file
    and basic statistics are written to ``temp/graph_stats.json``.  Missing
    dependencies or files simply result in the function returning quietly.
    """

    if nx is None:  # pragma: no cover - optional dependency
        return

    graph_path = Path("agents") / agent_uuid / "memory_graph.gexf"
    if not graph_path.exists():
        return

    try:
        g = nx.read_gexf(graph_path)  # type: ignore[arg-type]
    except Exception:
        return

    stats = {"nodes": g.number_of_nodes(), "edges": g.number_of_edges()}
    out_path = graph_path.parent / "temp" / "graph_stats.json"
    out_path.parent.mkdir(exist_ok=True)
    try:
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(stats, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


__all__ = ["log_global_metrics", "visualize_agent_graphs"]

```

## `rwmg/sim_runner/assign_behavior_profiles.py`

```python
"""Assign behaviour templates to existing agents."""

from __future__ import annotations

import json
from pathlib import Path

from rwmg.sim_runner.sim_start import assign_behavior_profile


def assign_profiles_to_existing_agents() -> None:
    """Populate ``agent_state.json`` with a behaviour profile if missing."""

    base = Path("agents")
    if not base.exists():
        return

    for agent_dir in base.iterdir():
        if not agent_dir.is_dir():
            continue
        state_path = agent_dir / "agent_state.json"
        try:
            with state_path.open("r", encoding="utf-8") as fh:
                state = json.load(fh) or {}
        except (OSError, json.JSONDecodeError):
            state = {}
        if state.get("behavior_profile"):
            continue
        state["behavior_profile"] = assign_behavior_profile(agent_dir.name)
        try:
            with state_path.open("w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass


if __name__ == "__main__":
    assign_profiles_to_existing_agents()

```

## `rwmg/sim_runner/epoch_runner.py`

```python
"""Orchestrate the simulation epochs."""

from __future__ import annotations
from pathlib import Path
from typing import Dict

from rwmg.lifecycle_manager.evolution_protocol import check_for_evolution
from rwmg.lifecycle_manager.main_loop import run_agent_day
from rwmg.lifecycle_manager.ritual_tracker import complete_ritual, start_ritual
from rwmg.sim_runner.agent_watcher import log_global_metrics


def run_epoch(agent_manifest: Dict, epoch_length: int) -> None:
    """Run ``epoch_length`` days of simulation for each agent.

    The function is intentionally defensive: misconfigured manifests or
    transient failures in a single agent must not halt the simulation.  Only
    agents marked as ``active`` and whose directories exist are processed.
    """

    if not isinstance(agent_manifest, dict) or epoch_length <= 0:
        return

    for day in range(epoch_length):
        for agent_uuid, meta in list(agent_manifest.items()):
            status = meta.get("status", "active") if isinstance(meta, dict) else "active"
            if status != "active":
                continue

            if not (Path("agents") / agent_uuid).exists():
                continue

            try:
                execute_daily_ritual(agent_uuid)
            except Exception:
                # Keep the epoch running even if an individual agent fails.
                continue

        log_global_metrics(agent_manifest, day)


def execute_daily_ritual(agent_uuid: str) -> None:

    """Perform the posting cycle and evolution check for ``agent_uuid``."""

    start_ritual(agent_uuid, "posting")
    try:
        run_agent_day(agent_uuid, 0)
    finally:
        complete_ritual(agent_uuid)

    try:
        check_for_evolution(agent_uuid)
    except Exception:
        # Evolution checks are best-effort; failures are ignored.
        pass


__all__ = ["run_epoch", "execute_daily_ritual"]

```

## `rwmg/sim_runner/multi_agent_controller.py`

```python

"""Coordinate tasks for multiple agents including social interactions.

In the broader simulation the controller is responsible for deciding what each
agent does during a simulation tick.  Posting and commenting are handled by
other modules; this controller adds support for **upvote** and **downvote**
interactions so that agents can react to existing content.

The implementation here is deliberately small and deterministic so that unit
tests can exercise the behaviour without relying on network calls or external
services.


"""Asynchronous multi-agent controller for RWMG simulations.

This module provides a light-weight orchestrator capable of running
hundreds of agents concurrently.  It is intentionally conservative and
uses best-effort operations so that missing configuration or unexpected
runtime errors do not halt the swarm.  The controller replaces the
``run_epoch`` approach with an event driven system.
"""Asynchronous multi-agent controller for the RWMG simulation.

This module replaces the sequential ``run_epoch`` loop with a stochastic
scheduler that dispatches agents over the course of a simulated day.  It is
purposefully lightweight yet provides hooks for behaviour profiling,
proxy isolation and basic cooldown management.


"""

from __future__ import annotations


from typing import Dict, Iterable, List

from rwmg.sim_runner.social_activity_simulator import evaluate_vote


def assign_tasks(agent_uuid: str, content_feed: Iterable[str]) -> Dict[str, List[Dict[str, str]]]:
    """Return a task bundle for ``agent_uuid`` based on ``content_feed``.

    The ``content_feed`` represents posts visible to the agent.  For each post
    the agent may choose to upvote or downvote depending on how the content
    aligns with its memories.  Neutral posts are ignored.  The returned
    dictionary currently only contains a ``"votes"`` entry but the structure
    mirrors what a fuller controller would provide when also scheduling posting
    or commenting tasks.
    """

    votes: List[Dict[str, str]] = []
    for post in content_feed:
        decision = evaluate_vote(agent_uuid, post)
        if decision != "neutral":
            votes.append({"action": decision, "post": post})

    return {"votes": votes}


__all__ = ["assign_tasks"]

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List

from rwmg.sim_runner.epoch_runner import execute_daily_ritual

try:  # ``yaml`` is an optional dependency in the test environment
    import yaml
except Exception:  # pragma: no cover - fallback when PyYAML is missing
    yaml = None  # type: ignore


# Controller state is stored at module level so that helper functions can
# operate without threading through a complex context object.
CONTROLLER_STATE: Dict[str, Dict[str, Any]] = {}


def _load_json(path: Path) -> Dict[str, Any]:
    """Best effort JSON loader returning an empty dict on failure."""

    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Return YAML content or ``{}`` when unavailable."""

    if yaml is None:
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:

import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

from rwmg.lifecycle_manager.evolution_protocol import check_for_evolution
from rwmg.lifecycle_manager.main_loop import run_agent_day
from rwmg.lifecycle_manager.ritual_tracker import complete_ritual, start_ritual
from rwmg.sim_runner.agent_watcher import log_global_metrics
from rwmg.utils.api_wrappers import _load_proxy_for_agent

# ---------------------------------------------------------------------------
# Helper loaders
# ---------------------------------------------------------------------------

def _load_posting_weight(agent_id: str) -> float:
    """Return the posting likelihood for ``agent_id``.

    The value is stored in ``agents/<id>/agent_state.json`` under the key
    ``posting_likelihood``.  Missing files or malformed JSON default to ``1``.
    """

    state_path = Path("agents") / agent_id / "agent_state.json"
    try:
        with state_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
            return float(data.get("posting_likelihood", 1))
    except Exception:
        return 1.0

def _load_behavior_profiles() -> Dict[str, Dict]:
    """Load optional behaviour profiles describing wake/sleep cycles."""

    config_path = Path("config") / "agent_behavior_profiles.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with config_path.open("r", encoding="utf-8") as fh:

            return yaml.safe_load(fh) or {}
    except Exception:
        return {}



def initialize_controller(
    agent_manifest: Dict[str, Any] | None = None,
    behavior_profiles: Dict[str, Any] | None = None,
) -> Dict[str, Dict[str, Any]]:
    """Load agents, attach behaviour profiles and prepare runtime state.

    Parameters
    ----------
    agent_manifest:
        Optional manifest data.  When ``None`` the content of
        ``agents/persona_manifest.json`` is used.
    behavior_profiles:
        Optional behaviour profile definitions.  When ``None`` the content
        of ``config/agent_behavior_profiles.yaml`` is loaded.
    """

    global CONTROLLER_STATE

    if agent_manifest is None:
        agent_manifest = _load_json(Path("agents") / "persona_manifest.json")

    if behavior_profiles is None:
        behavior_profiles = _load_yaml(Path("config") / "agent_behavior_profiles.yaml")

    proxies = _load_json(Path("secrets") / "proxies_map.json")

    state: Dict[str, Dict[str, Any]] = {}
    for agent_id, meta in (agent_manifest or {}).items():
        if not isinstance(meta, dict) or meta.get("status", "active") != "active":
            continue

        behaviour_name = meta.get("behavior_profile")
        behaviour = behavior_profiles.get(behaviour_name, {}) if behavior_profiles else {}

        state_path = Path("agents") / agent_id / "agent_state.json"
        agent_state = _load_json(state_path)
        agent_state.setdefault("behavior_profile", behaviour_name)
        agent_state.setdefault("behavior", behaviour)

        state[agent_id] = {
            "id": agent_id,
            "meta": meta,
            "agent_state": agent_state,
            "behavior": behaviour,
            "task_queue": [],
            "daily_log": [],
            "next_available_time": datetime.min,
            "proxy": proxies.get(agent_id),
            "state_path": state_path,
        }

    CONTROLLER_STATE = state
    return CONTROLLER_STATE


def _within_windows(current_time: datetime, windows: List[str]) -> bool:
    """Return ``True`` if ``current_time`` is inside any of ``windows``.

    Each window is expected in ``HH:MM-HH:MM`` or ``HH:MM–HH:MM`` format.  The
    function is resilient to malformed entries and simply ignores them.
    """

    if not windows:
        return True

    time_only = current_time.time()
    for window in windows:
        try:
            start_s, end_s = window.replace("–", "-").split("-")
            start_t = datetime.strptime(start_s, "%H:%M").time()
            end_t = datetime.strptime(end_s, "%H:%M").time()
        except Exception:
            continue
        if start_t <= time_only <= end_t:
            return True
    return False


def assign_tasks(agent: Dict[str, Any], current_time: datetime) -> None:
    """Enqueue a randomized task for ``agent`` when eligible."""

    state = agent.get("agent_state", {})
    next_avail = state.get("next_available_time")
    if isinstance(next_avail, str):
        try:
            next_avail_dt = datetime.fromisoformat(next_avail)
        except ValueError:
            next_avail_dt = datetime.min
    else:
        next_avail_dt = next_avail or datetime.min
    if current_time < next_avail_dt:
        return

    behaviour = agent.get("behavior", {})
    windows = behaviour.get("activity_windows", [])
    if windows and not _within_windows(current_time, windows):
        return

    posting_likelihood = state.get("posting_likelihood", 1.0)
    if random.random() > float(posting_likelihood or 0):
        return

    task = random.choice(["post", "comment", "engage", "observe", "check_feedback"])
    agent.setdefault("task_queue", []).append(task)


async def execute_task(agent_id: str, task_type: str) -> None:
    """Execute ``task_type`` for ``agent_id`` and update state."""

    agent = CONTROLLER_STATE.get(agent_id)
    if not agent:
        return

    state_path: Path = agent.get("state_path")
    state = agent.get("agent_state", {})
    behaviour = agent.get("behavior", {})

    # Perform the actual task.  Only ``post`` maps to a real routine; other
    # tasks are placeholders which simply yield to the event loop.
    try:
        if task_type == "post":
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, execute_daily_ritual, agent_id)
        else:  # simulate non-post tasks
            await asyncio.sleep(0)
    except Exception as exc:  # pragma: no cover - best effort logging
        logging.exception("task %s for %s failed: %s", task_type, agent_id, exc)

    now = datetime.utcnow()
    cooldown = behaviour.get("cooldown_period_hours", 0)
    if isinstance(cooldown, list) and cooldown:
        cooldown_hours = random.uniform(cooldown[0], cooldown[-1])
    else:
        try:
            cooldown_hours = float(cooldown)
        except Exception:
            cooldown_hours = 0.0

    next_available = now + timedelta(hours=cooldown_hours)

    state.update(
        {
            "last_task": task_type,
            "last_task_time": now.isoformat(),
            "cooldown_timer": cooldown_hours,
            "next_available_time": next_available.isoformat(),
        }
    )
    agent["agent_state"] = state
    agent.setdefault("daily_log", []).append({"task": task_type, "time": now.isoformat()})

    try:  # persist updated agent state
        with state_path.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
    except OSError:  # pragma: no cover - ignore FS errors
        pass


async def run_swarm(
    epoch_duration_hours: int = 24,
    tick_seconds: int = 10,
    time_scale: float = 1.0,
) -> None:
    """Run the swarm controller for ``epoch_duration_hours`` simulated hours.

    ``time_scale`` determines how many simulated seconds elapse per real
    second.  A value of ``12`` would make one simulated hour pass in five real
    minutes.  The function returns when the simulated time exceeds the desired
    duration.
    """

    start_real = datetime.utcnow()
    start_sim = start_real
    end_sim = start_sim + timedelta(hours=epoch_duration_hours)

    metrics = {agent_id: {"tasks": 0} for agent_id in CONTROLLER_STATE}

    while True:
        now_real = datetime.utcnow()
        sim_now = start_sim + (now_real - start_real) * time_scale
        if sim_now >= end_sim:
            break

        # Determine which agents should act this tick
        for agent in CONTROLLER_STATE.values():
            assign_tasks(agent, sim_now)

        jobs = []
        for agent_id, agent in CONTROLLER_STATE.items():
            queue = agent.get("task_queue", [])
            if queue:
                task_type = queue.pop(0)
                jobs.append(asyncio.create_task(execute_task(agent_id, task_type)))
                metrics[agent_id]["tasks"] += 1

        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

        await asyncio.sleep(tick_seconds / time_scale)

    # Persist metrics for external analysis
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"swarm_metrics_{start_sim.date().isoformat()}.json"
    try:
        with log_path.open("w", encoding="utf-8") as fh:
            json.dump(metrics, fh, indent=2, default=str)
    except OSError:  # pragma: no cover - best effort
        pass


__all__ = [
    "initialize_controller",
    "run_swarm",
    "assign_tasks",
    "execute_task",
]

def _is_awake(agent_id: str, minute_of_day: int, profiles: Dict[str, Dict]) -> bool:
    """Determine whether ``agent_id`` is awake at ``minute_of_day``."""

    profile = profiles.get(agent_id) or {}
    wake = int(profile.get("wake_hour", 0)) * 60
    sleep = int(profile.get("sleep_hour", 24)) * 60
    if wake <= sleep:
        return wake <= minute_of_day < sleep
    # handle cycles that wrap past midnight
    return minute_of_day >= wake or minute_of_day < sleep

# ---------------------------------------------------------------------------
# Core async execution
# ---------------------------------------------------------------------------

async def _execute_agent(agent_id: str) -> None:
    """Run the daily ritual for ``agent_id`` with retry protection."""

    proxy = _load_proxy_for_agent(agent_id)
    if proxy is None:
        # Without a proxy we skip to maintain network isolation expectations
        return

    start_ritual(agent_id, "posting")
    try:
        for attempt in range(3):
            try:
                run_agent_day(agent_id, 0, proxies=proxy)
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(1)
        try:
            check_for_evolution(agent_id)
        except Exception:
            pass
    finally:
        complete_ritual(agent_id)


async def run_simulated_day(
    agent_manifest: Dict[str, Dict],
    timestep_minutes: int = 10,
    real_time: bool = False,
) -> None:
    """Run a 24 hour simulation period for ``agent_manifest`` asynchronously."""

    behaviour_profiles = _load_behavior_profiles()
    cooldown: Dict[str, datetime] = {}
    start_time = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    current_time = start_time
    end_time = start_time + timedelta(days=1)

    while current_time < end_time:
        minute_of_day = int((current_time - start_time).total_seconds() // 60)

        # Determine eligible agents and their weights
        candidates: List[str] = []
        weights: List[float] = []
        for agent_id in agent_manifest:
            if not _is_awake(agent_id, minute_of_day, behaviour_profiles):
                continue
            if cooldown.get(agent_id, start_time) > current_time:
                continue
            candidates.append(agent_id)
            weights.append(_load_posting_weight(agent_id))

        if candidates:
            sample_size = max(1, int(len(candidates) * 0.1))
            selected = set(random.choices(candidates, weights=weights, k=sample_size))
            tasks = [asyncio.create_task(_execute_agent(a)) for a in selected]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            cooldown_update = current_time + timedelta(minutes=30)
            for a in selected:
                cooldown[a] = cooldown_update

        log_global_metrics(agent_manifest, minute_of_day // 60)

        # Advan lo ce simulation clock
        current_time += timedelta(minutes=timestep_minutes)
        await asyncio.sleep(timestep_minutes * 60 if real_time else 0)


__all__ = ["run_simulated_day"]

```

## `rwmg/sim_runner/sim_start.py`

```python
"""Utilities for bootstrapping the simulation by creating agents.

The real project would involve a fairly involved pipeline for birthing an
agent – generating a persona, seeding memories and provisioning credentials.
For the purposes of the unit tests we implement a much lighter version that is
still file‑system driven and therefore compatible with the rest of the
modules.  Each created agent receives a directory under ``agents/`` containing
basic placeholder files so downstream functions can operate without failing.
"""

from __future__ import annotations

import json
import random
import uuid
from pathlib import Path
from typing import Dict, List

import yaml

from rwmg.utils.timestamp_utils import get_current_iso_time


def _load_behavior_profiles() -> Dict[str, Dict]:
    """Read behaviour templates from the config file."""

    config_path = (
        Path(__file__).resolve().parent.parent / "config" / "agent_behavior_profiles.yaml"
    )
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return {}


BEHAVIOR_PROFILES = _load_behavior_profiles()


def assign_behavior_profile(agent_id: str) -> str:
    """Randomly choose a behaviour profile for ``agent_id``."""

    if not BEHAVIOR_PROFILES:
        return ""
    return random.choice(list(BEHAVIOR_PROFILES.keys()))


# sim_runner/sim_start.py
def create_agents(count: int, archetype_config: Dict, platform_config: Dict) -> Dict[str, str]:
    """Initialise a number of simple agents on disk.

    Parameters
    ----------
    count:
        Number of agents to create.  Values below zero result in an empty
        mapping being returned.
    archetype_config:
        Configuration describing available archetypes.  The function expects a
        list under ``"archetypes"`` or, alternatively, uses the dictionary keys
        as the available archetypes.  If no archetype information is supplied a
        default of ``"Lover"`` is used.
    platform_config:
        Used to derive basic communication details for the agent.  Currently
        only an ``"email_domain"`` key is observed.

    Returns
    -------
    Dict[str, str]
        Mapping of generated agent UUIDs to their display names.
    """

    agents: Dict[str, str] = {}
    if count <= 0:
        return agents

    # Determine available archetypes; tolerate a variety of config shapes.
    if isinstance(archetype_config, dict):
        if isinstance(archetype_config.get("archetypes"), list):
            archetypes = list(archetype_config["archetypes"])
        else:
            archetypes = list(archetype_config.keys())
    elif isinstance(archetype_config, list):
        archetypes = list(archetype_config)
    else:
        archetypes = []
    if not archetypes:
        archetypes = ["Lover"]

    email_domain = str(platform_config.get("email_domain", "example.com"))

    root_dir = Path(__file__).resolve().parent.parent
    base_agents = root_dir / "agents"
    secrets_dir = root_dir / "secrets" / "agent_keys"
    secrets_dir.mkdir(parents=True, exist_ok=True)

    for _ in range(count):
        agent_id = uuid.uuid4().hex
        archetype = random.choice(archetypes)
        name = f"Agent-{agent_id[:8]}"

        agent_dir = base_agents / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)

        profile = {
            "agent_id": agent_id,
            "name": name,
            "birthday": get_current_iso_time().split("T")[0],
            "archetype_core": archetype,
            "email": f"{agent_id}@{email_domain}",
            "secrets_path": str(secrets_dir / f"{agent_id}.json"),
        }

        # Persist the profile and a handful of stub files used by other
        # modules.  Failures in writing non‑critical files are ignored so that
        # agent creation remains best‑effort.
        try:
            with (agent_dir / "profile.json").open("w", encoding="utf-8") as fh:
                json.dump(profile, fh, ensure_ascii=False, indent=2)

            behavior = assign_behavior_profile(agent_id)
            placeholders = {
                "agent_state.json": {"behavior_profile": behavior},
                "canonical_events.json": [],
                "memory_log.json": [],
                "memory_cache_top5.json": [],
                "suppression_log.json": [],
                "memory_tags.json": {},
                "connections.json": {"friends": [], "mentors": []},
            }
            for filename, default in placeholders.items():
                with (agent_dir / filename).open("w", encoding="utf-8") as fh:
                    json.dump(default, fh, ensure_ascii=False, indent=2)

            (agent_dir / "memory_index.csv").touch()
            (agent_dir / "temp").mkdir(exist_ok=True)

            secret_path = secrets_dir / f"{agent_id}.json"
            if not secret_path.exists():
                with secret_path.open("w", encoding="utf-8") as fh:
                    json.dump(
                        {
                            "email_provider": "",
                            "smtp_user": "",
                            "smtp_pass": "",
                        },
                        fh,
                        ensure_ascii=False,
                        indent=2,
                    )
        except OSError:
            # Ignore file system errors to keep agent creation resilient.
            pass

        agents[agent_id] = name

    return agents


def populate_manifest(agent_uuids: List[str]) -> None:
    """Create or update the global agent manifest.

    The manifest lives at ``agents/persona_manifest.json`` and stores a small
    amount of metadata for each active agent.  Existing entries are preserved
    and updated when a UUID reappears in ``agent_uuids``.
    """

    root_dir = Path(__file__).resolve().parent.parent
    manifest_path = root_dir / "agents" / "persona_manifest.json"

    try:
        with manifest_path.open("r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        if not isinstance(manifest, dict):
            manifest = {}
    except (OSError, json.JSONDecodeError):
        manifest = {}

    for agent_id in agent_uuids:
        profile_path = root_dir / "agents" / agent_id / "profile.json"
        try:
            with profile_path.open("r", encoding="utf-8") as fh:
                profile = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue

        manifest[agent_id] = {
            "name": profile.get("name", ""),
            "archetype_core": profile.get("archetype_core", ""),
            "email": profile.get("email", ""),
            "status": "active",
            "created_at": get_current_iso_time(),
        }

    try:
        with manifest_path.open("w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
    except OSError:
        # Failure to persist the manifest is non-fatal for the simulation.
        pass

```

## `rwmg/sim_runner/social_activity_simulator.py`

```python
"""Simulate social interactions such as upvotes and downvotes.

The wider RWMG experiment models online communities where agents react to
content created by their peers.  This module provides a minimal implementation
of that behaviour: given a piece of text and an agent identifier it determines
whether the agent would upvote, downvote or ignore the post.  The decision is
based on the agent's stored memories which already encode resonance and
suppression information.

The rules implemented here are intentionally lightweight:

* If the post shares vocabulary with one of the agent's *top memories* it is
  considered aligned and will receive an **upvote**.
* If vocabulary overlaps with any entry in the agent's *suppression log* the
  post is considered discordant and will be **downvoted**.
* If neither condition holds the agent remains neutral and takes no action.

The goal is not to perfectly model social behaviour but to provide a deterministic
mechanism that other components – particularly the multi‑agent controller – can
leverage when simulating social feedback in a test environment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

from rwmg.utils.memory_extractor import rank_memories


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _load_texts(path: Path, key: str) -> List[str]:
    """Return a list of texts stored under ``key`` in ``path``.

    Missing files or malformed content result in an empty list; this keeps the
    caller's logic straightforward and mirrors the defensive style used
    throughout the code base.
    """

    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []

    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return [str(item.get(key, "")) for item in data if item.get(key)]
        return [str(item) for item in data if item]
    return []


def _tokenise(texts: Sequence[str]) -> set:
    """Tokenise a sequence of texts into a case‑insensitive word set."""

    tokens = set()
    for text in texts:
        tokens.update(text.lower().split())
    return tokens


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_vote(agent_uuid: str, post_content: str) -> str:
    """Determine whether ``agent_uuid`` would upvote or downvote ``post_content``.

    The function refreshes the agent's memory rankings to ensure caches are up
    to date, then compares the vocabulary of the ``post_content`` against the
    agent's high‑resonance memories and suppression log.

    Parameters
    ----------
    agent_uuid:
        Identifier of the agent performing the evaluation.
    post_content:
        Text content of the post under consideration.

    Returns
    -------
    str
        One of ``"upvote"``, ``"downvote"`` or ``"neutral"``.
    """

    if not post_content:
        return "neutral"

    # Ensure memory caches are refreshed before reading them
    try:
        rank_memories(agent_uuid)
    except Exception:
        pass

    agent_dir = Path("agents") / agent_uuid
    top_memories = _load_texts(agent_dir / "memory_cache_top5.json", "content")
    bottom_memories = _load_texts(agent_dir / "suppression_log.json", "content")

    post_tokens = set(post_content.lower().split())
    top_tokens = _tokenise(top_memories)
    bottom_tokens = _tokenise(bottom_memories)

    if post_tokens & top_tokens:
        return "upvote"
    if post_tokens & bottom_tokens:
        return "downvote"
    return "neutral"


def apply_votes(agent_uuid: str, posts: Iterable[str]) -> List[Tuple[str, str]]:
    """Return vote actions for ``posts`` made by ``agent_uuid``.

    Each element of ``posts`` is analysed via :func:`evaluate_vote`.  A list of
    tuples ``(post, action)`` is returned for every post that results in an
    ``"upvote"`` or ``"downvote"``.  Neutral outcomes are omitted to keep the
    output concise for downstream consumers.
    """

    results: List[Tuple[str, str]] = []
    for post in posts:
        action = evaluate_vote(agent_uuid, post)
        if action != "neutral":
            results.append((post, action))
    return results


__all__ = ["evaluate_vote", "apply_votes"]
```

## `rwmg/social/__init__.py`

```python
"""Social discovery utilities."""

__all__ = []
```

## `rwmg/social/community_discovery.py`

```python
"""Utilities for discovering and prioritizing Reddit communities."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

import requests


def _extract_keywords(persona_meta: Dict, memory_weights: List[str]) -> List[str]:
    """Collect keyword tokens from persona metadata and memory contents."""
    keywords: List[str] = []

    meta_sources = [
        persona_meta.get("archetype"),
        persona_meta.get("mood"),
        persona_meta.get("description"),
        persona_meta.get("persona_description"),
        persona_meta.get("bio"),
    ]
    interests = persona_meta.get("interests")
    if isinstance(interests, list):
        meta_sources.extend(interests)
    elif isinstance(interests, str):
        meta_sources.append(interests)

    for src in meta_sources:
        if isinstance(src, str):
            keywords.extend(re.findall(r"\w+", src.lower()))

    for mem in memory_weights or []:
        if isinstance(mem, str):
            keywords.extend(re.findall(r"\w+", mem.lower()))

    # deduplicate while preserving order
    deduped: List[str] = []
    seen = set()
    for word in keywords:
        if word not in seen:
            deduped.append(word)
            seen.add(word)
    return deduped


def discover_subreddits(agent_id: str, persona_meta: Dict, memory_weights: List[str]) -> List[Dict[str, float]]:
    """Return candidate subreddits with relevance scores."""
    keywords = _extract_keywords(persona_meta, memory_weights)
    scores: Dict[str, float] = {}
    for kw in keywords:
        if not kw:
            continue
        try:
            resp = requests.get(
                "https://www.reddit.com/subreddits/search.json",
                params={"q": kw, "limit": 5},
                headers={"User-Agent": f"rwm-agent-{agent_id}"},
                timeout=5,
            )
            data = resp.json().get("data", {})
            for child in data.get("children", []):
                info = child.get("data", {})
                name = info.get("display_name")
                if not name:
                    continue
                subs = float(info.get("subscribers", 0) or 0)
                active = float(info.get("active_user_count", 0) or 0)
                relevance = 1.0 + subs / 1_000_000 + active / 100_000
                prev = scores.get(name, 0.0)
                scores[name] = max(prev, relevance)
        except Exception:
            continue

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [{"subreddit": name, "score": score} for name, score in ordered]


def update_interest_graph(agent_id: str, subreddit: str, resonance_score: float) -> None:
    """Update the agent's interest graph with new interaction data."""
    path = Path("agents") / agent_id / "interest_graph.json"
    try:
        with path.open("r", encoding="utf-8") as fh:
            graph = json.load(fh)
    except (OSError, json.JSONDecodeError):
        graph = {}

    node = graph.get(subreddit, {"interactions": 0, "avg_resonance": 0.0})
    interactions = int(node.get("interactions", 0)) + 1
    avg = float(node.get("avg_resonance", 0.0))
    avg = (avg * (interactions - 1) + float(resonance_score)) / interactions
    node.update({"interactions": interactions, "avg_resonance": avg})
    graph[subreddit] = node

    # Apply decay to other subreddits
    for name, stats in graph.items():
        if name == subreddit:
            continue
        stats["avg_resonance"] = float(stats.get("avg_resonance", 0.0)) * 0.95

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(graph, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass


def choose_target_community(agent_id: str, persona_meta: Dict, memory_weights: List[str]) -> str:
    """Select the most promising subreddit for the agent's next action."""
    candidates = discover_subreddits(agent_id, persona_meta, memory_weights)

    blacklist: List[str] = []
    blacklist_path = Path("config") / "subreddit_blacklist.json"
    try:
        with blacklist_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, list):
                blacklist = [s.lower() for s in data]
    except (OSError, json.JSONDecodeError):
        blacklist = []

    filtered = [c for c in candidates if c["subreddit"].lower() not in blacklist]
    if not filtered:
        return ""

    ig_path = Path("agents") / agent_id / "interest_graph.json"
    try:
        with ig_path.open("r", encoding="utf-8") as fh:
            interest_graph = json.load(fh)
    except (OSError, json.JSONDecodeError):
        interest_graph = {}

    def combined_score(entry: Dict[str, float]) -> float:
        base = entry.get("score", 0.0)
        bonus = float(interest_graph.get(entry["subreddit"], {}).get("avg_resonance", 0.0))
        return base + bonus

    best = max(filtered, key=combined_score, default=None)
    return best["subreddit"] if best else ""


__all__ = [
    "discover_subreddits",
    "update_interest_graph",
    "choose_target_community",
]
```

## `rwmg/utils/api_wrappers.py`

```python
"""Wrappers around external APIs used by the simulation.

The functions in this module provide thin abstractions over HTTP endpoints for the
Gemini LLM API and various social platforms.  They are intentionally lightweight
so that higher level modules can mock or swap them easily during testing.

Both helpers read API keys from ``secrets/platform_keys.json`` which lives at the
root of the repository.  The file is expected to contain keys such as
``gemini_api_key`` and platform tokens.  A small amount of defensive programming
is included so that missing keys or network failures raise informative errors.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

try:  # pragma: no cover - import is trivial but may fail in minimal envs
    import requests
except Exception:  # pragma: no cover - handled at call time
    requests = None


_ROOT = Path(__file__).resolve().parents[1]


def _load_platform_keys() -> Dict[str, str]:
    """Return the dictionary of platform API keys.

    Missing or malformed files yield an empty dictionary which callers can handle
    gracefully.  This function is separated for ease of mocking during tests.
    """

    key_path = _ROOT / "secrets" / "platform_keys.json"
    try:
        with key_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load_proxy_for_agent(agent_id: str) -> Optional[Dict[str, str]]:
    """Return the proxy configuration for ``agent_id``.

    The proxies are stored in ``secrets/proxies_map.json`` using the format

    ``{"agent_id": "http://user:pass@proxy:port"}``.

    Parameters
    ----------
    agent_id:
        Identifier for the agent whose proxy should be loaded.

    Returns
    -------
    Optional[Dict[str, str]]
        A ``requests`` compatible proxies dictionary or ``None`` if no mapping
        exists.
    """

    proxy_path = _ROOT / "secrets" / "proxies_map.json"
    try:
        with proxy_path.open("r", encoding="utf-8") as fh:
            mapping = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    proxy_url = mapping.get(agent_id)
    if not proxy_url:
        return None

    return {"http": proxy_url, "https": proxy_url}


def call_gemini_api(prompt: str, proxies: Optional[Dict[str, str]] = None) -> str:
    """Send ``prompt`` to the Gemini API and return the model's text output.

    Parameters
    ----------
    prompt:
        The textual prompt to submit to Gemini.

    Returns
    -------
    str
        The model generated text.

    Raises
    ------
    RuntimeError
        If the request fails or the API key is missing.
    """

    if requests is None:  # pragma: no cover - trivial in tests
        raise RuntimeError("The 'requests' library is required to call the Gemini API")

    api_key = _load_platform_keys().get("gemini_api_key")
    if not api_key:
        raise RuntimeError("Gemini API key not found in secrets/platform_keys.json")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-pro:generateContent?key={api_key}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        response = requests.post(url, json=payload, timeout=10, proxies=proxies)
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as exc:  # pragma: no cover - network may be unavailable
        raise RuntimeError(f"Gemini API request failed: {exc}") from exc


def post_to_platform(
    platform: str,
    content: str,
    auth_token: str,
    proxies: Optional[Dict[str, str]] = None,
) -> str:
    """Publish ``content`` to a social platform.

    The function currently supports a small set of platforms.  It issues a
    ``POST`` request to the appropriate endpoint and returns the event or message
    identifier supplied by the remote service.

    Parameters
    ----------
    platform:
        Name of the platform (e.g. ``"twitter"`` or ``"reddit"``).
    content:
        The text content to publish.
    auth_token:
        The bearer token or API key required by the platform.

    Returns
    -------
    str
        Identifier for the created post as reported by the platform.
    """

    if requests is None:  # pragma: no cover - trivial in tests
        raise RuntimeError("The 'requests' library is required to post to platforms")

    endpoints = {
        "twitter": ("https://api.twitter.com/2/tweets", {"text": content}),
        "reddit": (
            "https://oauth.reddit.com/api/submit",
            {"kind": "self", "sr": "", "title": content[:40], "text": content},
        ),
        "discord": (
            # The caller must include the channel ID in the token or content; this
            # placeholder endpoint demonstrates the pattern only.
            "https://discord.com/api/v10/channels/CHANNEL_ID/messages",
            {"content": content},
        ),
    }

    platform_lower = platform.lower()
    if platform_lower not in endpoints:
        raise ValueError(f"Unsupported platform: {platform}")

    url, payload = endpoints[platform_lower]
    headers = {"Authorization": f"Bearer {auth_token}"}

    try:
        resp = requests.post(
            url, json=payload, headers=headers, timeout=10, proxies=proxies
        )
        resp.raise_for_status()
        data = resp.json()
        # common id fields used by different platforms
        return (
            data.get("id")
            or data.get("data", {}).get("id")
            or data.get("post_id", "")
        )
    except Exception as exc:  # pragma: no cover - network may be unavailable
        raise RuntimeError(f"Posting to {platform} failed: {exc}") from exc


__all__ = [
    "call_gemini_api",
    "post_to_platform",
    "_load_platform_keys",
    "_load_proxy_for_agent",
]

```

## `rwmg/utils/diversity_calculator.py`

```python
"""Utilities for computing memory tag diversity bonuses."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import List


RECENT_WINDOW = 50  # Number of most recent memories to consider when scoring


def compute_diversity_bonus(memory_tags: List[str], agent_uuid: str) -> float:
    """Return a small bonus encouraging thematic diversity.

    The bonus is derived from the rarity of the provided ``memory_tags`` when
    compared against the agent's existing tag usage. Only the tags from the
    agent's most recent memories are considered (up to ``RECENT_WINDOW``
    entries) so that long forgotten themes do not permanently penalise new
    experiences. Tags that rarely appear in this recent set receive a higher
    bonus, while frequently used tags yield little to no bonus. The result is a
    value between ``0`` and ``0.3`` which can be added to a memory's weight
    prior to ranking.

    Parameters
    ----------
    memory_tags:
        Tags generated for the candidate memory.
    agent_uuid:
        Identifier used to locate the agent's stored tag history.

    Returns
    -------
    float
        A diversity bonus in the range ``0``–``0.3``. ``0`` indicates that all
        tags are already common in the agent's recent memory set, while ``0.3``
        denotes entirely novel tags.
    """

    if not memory_tags:
        return 0.0

    tags_path = Path("agents") / agent_uuid / "memory_tags.json"
    if not tags_path.exists():
        # No historical tag data – treat all tags as novel.
        return 0.3

    try:
        with tags_path.open("r", encoding="utf-8") as f:
            tag_map = json.load(f)
    except Exception:
        # If the file can't be read or is invalid, fall back to no bonus to
        # avoid unpredictable behaviour.
        return 0.0

    # Determine which event IDs are considered "recent".  If ``memory_log`` is
    # available we use its chronological ordering, otherwise we fall back to all
    # known tags.
    log_path = Path("agents") / agent_uuid / "memory_log.json"
    recent_event_ids: List[str] = []
    if log_path.exists():
        try:
            with log_path.open("r", encoding="utf-8") as f:
                log_entries = json.load(f)
            # Sort by timestamp to guard against out-of-order logs and then take
            # the most recent ``RECENT_WINDOW`` entries.
            log_entries.sort(key=lambda e: e.get("timestamp", ""))
            recent_event_ids = [e.get("event_id") for e in log_entries[-RECENT_WINDOW:]]
        except Exception:
            recent_event_ids = []

    if recent_event_ids:
        recent_tags = [tag for eid in recent_event_ids for tag in tag_map.get(eid, [])]
    else:
        # Fall back to all stored tags if we cannot determine recency.
        recent_tags = [tag for tags in tag_map.values() for tag in tags]

    if not recent_tags:
        return 0.3

    tag_counts = Counter(recent_tags)
    total = sum(tag_counts.values())
    if not total:
        return 0.3

    # Compute rarity for each candidate tag: 1 minus its relative frequency.
    rarity_scores = []
    for tag in set(memory_tags):  # Deduplicate incoming tags for fairness
        frequency = tag_counts.get(tag, 0) / total
        rarity_scores.append(1 - frequency)

    avg_rarity = sum(rarity_scores) / len(rarity_scores)

    # Scale to a maximum bonus of 0.3 and clamp to [0, 0.3].
    bonus = max(0.0, min(0.3, avg_rarity * 0.3))
    return bonus

```

## `rwmg/utils/event_bus.py`

```python
"""Simple in-memory publish/subscribe event bus.

The project utilises a light‑weight event system so that disparate
components can communicate without holding direct references to each
other.  The implementation here intentionally avoids any third‑party
dependencies and keeps state in memory, which is sufficient for the unit
tests and small simulations run inside this repository.

The bus offers two basic operations:

``emit_event(event_type, payload)``
    Immediately invokes all handlers previously registered for the given
    ``event_type``.  Handlers are executed synchronously in the order they
    were registered.  Any exceptions raised by a handler are logged and do
    not stop subsequent handlers from running.

``subscribe_to_event(event_type, handler)``
    Registers ``handler`` to be called whenever ``event_type`` is emitted.
    Handlers are stored once; duplicate registrations are ignored.
"""

from __future__ import annotations

from collections import defaultdict
from threading import Lock
from typing import Callable, DefaultDict, Dict, List

import logging

_LOGGER = logging.getLogger(__name__)

#
# Internal storage for the event bus.  A lock guards mutations so that the
# bus is safe to use in multi‑threaded environments.
#
_subscribers: DefaultDict[str, List[Callable[[Dict], None]]] = defaultdict(list)
_lock = Lock()


def emit_event(event_type: str, payload: Dict) -> None:
    """Publishes a new event to all subscribers.

    Parameters
    ----------
    event_type:
        The string identifier representing the type of event.
    payload:
        Arbitrary data associated with the event.  The payload is passed as
        the sole argument to each subscribed handler.
    """

    # Snapshot the handlers under a lock to prevent race conditions if
    # subscriptions change during iteration.
    with _lock:
        handlers = list(_subscribers.get(event_type, []))

    for handler in handlers:
        try:
            handler(payload)
        except Exception:  # pragma: no cover - defensive logging path
            _LOGGER.exception("Error in event handler for %s", event_type)


def subscribe_to_event(event_type: str, handler: Callable[[Dict], None]) -> None:
    """Registers ``handler`` to be called when ``event_type`` is emitted.

    Duplicate registrations are ignored so the handler will only be called
    once per event emission.
    """

    with _lock:
        if handler not in _subscribers[event_type]:
            _subscribers[event_type].append(handler)

```

## `rwmg/utils/math_functions.py`

```python
"""Reusable mathematical helpers used across the project.

Only two functions are required for the current codebase but they encapsulate
logic that is referenced from multiple modules.  The functions are intentionally
independent from the rest of the system to keep them easily testable.
"""

from __future__ import annotations

import math


# utils/math_functions.py
def nonlinear_trait_shift(current_value: float, delta: float) -> float:
    """Apply a bounded non-linear shift to a trait value.

    The raw trait values in the project are expected to fall within the
    ``[0, 1]`` interval.  When applying a shift we want large deltas to taper off
    near the extremes rather than clipping abruptly.  To achieve this we first
    add ``delta`` to ``current_value`` and then squash the result through a
    smooth ``tanh`` curve which asymptotically approaches ``0`` and ``1``.

    Parameters
    ----------
    current_value:
        The original trait value in the ``[0, 1]`` range.
    delta:
        The proposed change which may be positive or negative.

    Returns
    -------
    float
        The adjusted value constrained to ``[0, 1]`` with diminishing returns
        close to the boundaries.
    """

    # Shift the value and map it to the ``[-1, 1]`` domain for ``tanh``.
    shifted = (current_value + delta) * 2.0 - 1.0
    squashed = math.tanh(shifted)
    # Map back to ``[0, 1]`` and clamp for numerical safety.
    result = (squashed + 1.0) / 2.0
    return max(0.0, min(1.0, result))


def exponential_decay(value: float, time: int, half_life: int) -> float:
    """Return ``value`` after exponential decay over ``time`` units.

    ``half_life`` represents the number of time steps after which the value is
    expected to have halved.  The implementation follows the classic exponential
    decay formula ``value * 0.5 ** (time / half_life)``.  ``time`` and
    ``half_life`` are treated as non-negative; if ``half_life`` is zero the
    function degrades gracefully by returning ``0.0`` for any positive
    ``time``.

    Parameters
    ----------
    value:
        Initial value before decay.
    time:
        Number of time steps elapsed.  Negative values are treated as zero.
    half_life:
        The half-life period of the decay function.

    Returns
    -------
    float
        The decayed value.
    """

    if half_life <= 0:
        # With no half-life the value effectively drops to zero immediately
        # (except when no time has passed).
        return 0.0 if time > 0 else float(value)

    time = max(0, time)
    decay_factor = 0.5 ** (time / float(half_life))
    return float(value) * decay_factor

```

## `rwmg/utils/memory_extractor.py`

```python
"""Utilities for ranking an agent's memories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

from .diversity_calculator import compute_diversity_bonus
from .math_functions import exponential_decay
from .timestamp_utils import calculate_age_in_days


# utils/memory_extractor.py
def rank_memories(agent_uuid: str, top_k: int = 5, bottom_k: int = 3) -> Tuple[List[str], List[str]]:
    """Return top and bottom ranked memory texts for an agent.

    Each memory's weight is derived from its stored ``resonance_score`` which is
    reduced over time using an exponential decay and then adjusted by a small
    diversity bonus based on the rarity of its tags.  The resulting weight is
    clamped to the ``[0, 1]`` range and used for ranking.

    Parameters
    ----------
    agent_uuid:
        Identifier for the agent whose memories should be ranked.
    top_k:
        Number of highest weighted memories to return.  Defaults to ``5``.
    bottom_k:
        Number of lowest weighted memories to return.  Defaults to ``3``.

    Returns
    -------
    Tuple[List[str], List[str]]
        Two lists containing the memory ``content`` of the top ``top_k`` and
        bottom ``bottom_k`` memories respectively.  Lists may be shorter if the
        agent has fewer stored memories or if files are missing/invalid.
    """

    log_path = Path("agents") / agent_uuid / "memory_log.json"
    if not log_path.exists():
        return [], []

    try:
        with log_path.open("r", encoding="utf-8") as fh:
            log_entries = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return [], []

    tags_path = Path("agents") / agent_uuid / "memory_tags.json"
    try:
        with tags_path.open("r", encoding="utf-8") as fh:
            tag_map = json.load(fh)
    except (json.JSONDecodeError, OSError):
        tag_map = {}

    # store (weight, content, event_id) for downstream caching
    scored: List[Tuple[float, str, str]] = []

    for entry in log_entries:
        event_id = entry.get("event_id")
        content = entry.get("content", "")
        if not content:
            continue

        resonance = float(entry.get("resonance_score", 0.0))
        timestamp = entry.get("timestamp")
        age_days = calculate_age_in_days(timestamp) if timestamp else 0

        decayed = exponential_decay(resonance, age_days, half_life=30)
        tags = tag_map.get(event_id, [])
        diversity_bonus = compute_diversity_bonus(tags, agent_uuid)

        weight = max(0.0, min(1.0, decayed + diversity_bonus))
        scored.append((weight, content, event_id))

    if not scored:
        return [], []

    scored.sort(key=lambda x: x[0])  # ascending by weight

    # Split into top and bottom groups retaining weight and ids
    bottom_entries = scored[:bottom_k]
    top_entries = list(reversed(scored[-top_k:]))

    # Persist the top memories for quick recall
    cache_path = Path("agents") / agent_uuid / "memory_cache_top5.json"
    cache_payload = [
        {"event_id": eid, "content": text, "weight": weight}
        for weight, text, eid in top_entries
    ]
    try:
        with cache_path.open("w", encoding="utf-8") as fh:
            json.dump(cache_payload, fh, ensure_ascii=False, indent=2)
    except OSError:
        pass

    # Append/update suppression log with lowest weighted memories
    suppression_path = Path("agents") / agent_uuid / "suppression_log.json"
    try:
        with suppression_path.open("r", encoding="utf-8") as fh:
            suppression_log = json.load(fh)
    except (json.JSONDecodeError, OSError):
        suppression_log = []

    existing = {entry.get("event_id"): entry for entry in suppression_log}
    for weight, text, eid in bottom_entries:
        existing[eid] = {"event_id": eid, "content": text, "weight": weight}

    try:
        with suppression_path.open("w", encoding="utf-8") as fh:
            json.dump(list(existing.values()), fh, ensure_ascii=False, indent=2)
    except OSError:
        pass

    bottom = [content for _, content, _ in bottom_entries]
    top = [content for _, content, _ in top_entries]
    return top, bottom


```

## `rwmg/utils/tagger.py`

```python
"""Utility for deriving tags from memory content.

The function implemented here performs a very small scale natural language
processing task.  It relies solely on lightweight keyword matching so that it
works in constrained execution environments.  The goal is not to provide an
exhaustive taxonomy but to surface a reasonable set of thematic, emotional and
archetypal hints for subsequent scoring utilities.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

# ``yaml`` is an optional dependency.  The project can operate without it and
# fall back to a small built‑in mapping.
try:  # pragma: no cover - exercised indirectly
    import yaml  # type: ignore
except Exception:  # ModuleNotFoundError or any other issue
    yaml = None  # type: ignore


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _load_archetype_keywords() -> Dict[str, List[str]]:
    """Load archetype specific keywords from ``config/archetype_rules.yaml``.

    When the configuration or the YAML parser is unavailable a default mapping
    mirroring the specification is used.  Only the ``keywords`` entries are
    relevant for tag extraction.
    """

    config_path = Path(__file__).resolve().parents[1] / "config" / "archetype_rules.yaml"

    # Fallback mapping used when the file or parser is missing
    fallback = {
        "king": ["sovereignty", "order", "duty", "responsibility", "legacy"],
        "lover": ["intimacy", "betrayal", "longing", "yearning", "vulnerability"],
        "warrior": ["discipline", "struggle", "victory", "force", "courage"],
        "magician": ["insight", "system", "pattern", "knowledge", "transformation"],
    }

    if yaml is None:
        return fallback

    try:
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return fallback

    keywords: Dict[str, List[str]] = {}
    for archetype, info in data.items():
        kws = [kw.lower() for kw in info.get("keywords", [])]
        keywords[archetype.lower()] = kws

    return keywords or fallback


ARCHETYPE_KEYWORDS = _load_archetype_keywords()

# Basic vocabulary for emotions reflected in ``agent_state.json``
EMOTION_KEYWORDS: Dict[str, List[str]] = {
    "joy": ["joy", "happy", "delight", "smile", "glad"],
    "anger": ["anger", "angry", "rage", "mad", "furious"],
    "grief": ["grief", "sad", "sorrow", "melancholy", "loss"],
    "contempt": ["contempt", "scorn", "disdain"],
    "affinity": ["love", "affection", "fond", "like", "care"],
    "stress": ["stress", "anxiety", "worried", "tense", "fear"],
}

# A small thematic vocabulary capturing common narrative motifs
THEME_KEYWORDS: Dict[str, List[str]] = {
    "childhood": ["childhood", "child", "kid", "school", "parent", "mother", "father"],
    "adolescence": ["adolescence", "teen", "teenage", "high school"],
    "abandonment": ["abandon", "abandoned", "left me", "deserted"],
    "attachment": ["attachment", "attach", "bond", "cling"],
    "peer_rejection": ["rejected", "rejection", "bully", "ostracised", "peer"],
    "humiliation": ["humiliat", "embarrass", "shame"],
    "vulnerability": ["vulnerab", "fragile", "weakness"],
    "betrayal": ["betray", "treachery", "backstab", "deceived"],
    "injury": ["injury", "wound", "scar", "hurt"],
    "success": ["success", "victory", "accomplish", "win"],
    "failure": ["fail", "failure", "lost", "lose"],
}

NEGATIVE_EMOTIONS = {"anger", "grief", "contempt", "stress"}


def extract_memory_tags(memory_content: str, agent_archetype: str) -> List[str]:
    """Extract thematic, emotional and archetypal tags from ``memory_content``.

    Parameters
    ----------
    memory_content:
        Raw textual description of the memory.
    agent_archetype:
        Core archetype of the agent (e.g. ``"King"``).

    Returns
    -------
    list[str]
        Sorted list of unique tags.  If no tags can be derived a single
        ``"misc"`` tag is returned as a fallback.
    """

    text = memory_content.lower()
    tags: set[str] = set()

    # --- emotion tags -----------------------------------------------------
    for tag, keywords in EMOTION_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            tags.add(tag)

    # --- thematic tags ----------------------------------------------------
    for tag, keywords in THEME_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            tags.add(tag)

    # --- archetypal tag ---------------------------------------------------
    archetype_key = (agent_archetype or "").lower()
    if archetype_key:
        keywords = ARCHETYPE_KEYWORDS.get(archetype_key, [])
        if any(kw in text for kw in keywords):
            # Shadow vs. core is determined heuristically by emotion polarity
            if tags & NEGATIVE_EMOTIONS:
                tags.add(f"{archetype_key}_shadow")
            else:
                tags.add(archetype_key)
        else:
            # Include the archetype tag regardless to retain context
            tags.add(archetype_key)

    if not tags:
        return ["misc"]

    return sorted(tags)

```

## `rwmg/utils/timestamp_utils.py`

```python
"""Timestamp utilities for the RWMG project.

This module centralises lightweight helpers for working with timestamps and
dates.  Only a small subset of functionality is required by the simulation,
so the implementations intentionally avoid additional dependencies in favour
of the standard library ``datetime`` module.
"""

from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional

# utils/timestamp_utils.py
def get_current_iso_time() -> str:
    """Returns the current timestamp in ISO 8601 format.

    The timestamp is expressed in UTC and excludes microseconds to keep the
    output stable for logging and file naming purposes.
    """

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def calculate_age_in_days(birth_date: str) -> int:
    """Calculates the number of days since a given birth date.

    Parameters
    ----------
    birth_date:
        An ISO 8601 formatted date string. If no timezone information is
        provided, the date is assumed to be in UTC.

    Returns
    -------
    int
        The number of full days that have elapsed between ``birth_date`` and
        the current moment.  If ``birth_date`` cannot be parsed or lies in the
        future, ``0`` is returned.
    """

    try:
        birth_dt = datetime.fromisoformat(birth_date)
    except (TypeError, ValueError):
        return 0

    if birth_dt.tzinfo is None:
        birth_dt = birth_dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    delta = now - birth_dt
    return max(delta.days, 0)
```

## `tests/test_memory_loop.py`

```python
from pathlib import Path

from rwmg.memory_loop import (
    MemoryEvent,
    ResonanceWeightedMemoryGraph,
    adaptive_exploration_rate,
    counterfactual_model_variation,
    counterfactual_evaluate,
    embed,
    evaluate,
    evaluate_model_quality,
    group_by_cluster,
    predict_expected_score,
    rank_model_variants,
    select_best_model,
    simulate_strategy,
)


def _loop(
    tmp_path: Path,
    *,
    agent_id: str = "agent",
    epsilon: float = 0.2,
    seed: int = 0,
) -> ResonanceWeightedMemoryGraph:
    return ResonanceWeightedMemoryGraph(
        agent_id=agent_id,
        root_dir=tmp_path,
        retention_factor=0.92,
        learning_rate=0.35,
        threshold=0.05,
        memory_token_cap=90,
        max_memories=3,
        epsilon=epsilon,
        gamma=0.5,
        temporal_window=4,
        deterministic_seed=seed,
    )


def _event(
    event_id: str,
    input_text: str,
    output_text: str,
    weight: float,
    timestamp: int,
    *,
    agent_id: str = "agent",
    event_type: str = "interaction",
    usage_count: int = 0,
    future_score: float = 0.0,
    expected_value: float = 0.0,
    recent_scores=None,
) -> MemoryEvent:
    return MemoryEvent(
        id=event_id,
        agent_id=agent_id,
        input=input_text,
        output=output_text,
        outcome_signal=0.0,
        weight=weight,
        timestamp=timestamp,
        type=event_type,
        embedding=embed(input_text),
        future_score=future_score,
        usage_count=usage_count,
        cluster_id="",
        expected_value=expected_value,
        variance=0.0,
        recent_scores=list(recent_scores or []),
    )


def test_composite_evaluator_returns_components_and_confidence():
    result = evaluate(
        "create structured policy answer",
        "Task: create structured policy answer\n"
        "Answer: concise, structured, specific, actionable.\n"
        "Steps: 1. assess context 2. verify outcome.",
        {"retrieved": [{"id": "memory"}]},
    )

    assert set(result) == {"score", "components", "confidence"}
    assert set(result["components"]) == {"relevance", "coherence", "usefulness"}
    assert -1.0 <= result["score"] <= 1.0
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["score"] > 0.5


def test_memory_loop_uses_one_canonical_store_with_policy_state(tmp_path):
    loop = _loop(tmp_path)
    loop.process("canonical memory policy")
    agent_dir = tmp_path / "agent"
    store = loop.store.load_store()
    event = next(iter(store["events"].values()))

    assert (agent_dir / "memory_store.json").exists()
    assert not (agent_dir / "memory_events.jsonl").exists()
    assert not (agent_dir / "memory_state.json").exists()
    assert not (agent_dir / "memory_cache_top5.json").exists()
    assert event["embedding"]
    assert event["type"] == "interaction"
    assert "policy_state" in store
    assert "clusters" in store
    assert "expected_value" in event
    assert "variance" in event
    assert "recent_scores" in event


def test_persistence_survives_restart_and_affects_output(tmp_path):
    first = _loop(tmp_path)
    baseline = first.process("draft memory policy")

    restarted = _loop(tmp_path)
    changed = restarted.process("draft memory policy")

    assert changed != baseline
    assert "Pattern:" in changed
    assert restarted.last_trace["retrieved"] == [first.last_event_id]


def test_semantic_retrieval(tmp_path):
    loop = _loop(tmp_path)
    loop.store.append_event(
        _event(
            "car",
            "write vehicle safety policy",
            "Answer: clear structured vehicle safety policy",
            0.9,
            1,
        )
    )
    loop.store.append_event(
        _event("pasta", "cook tomato pasta", "Answer: pasta sauce", 1.0, 2)
    )

    retrieved = loop.retrieval_set("compose automobile safety guidelines")

    assert retrieved[0]["id"] == "car"
    assert retrieved[0]["similarity"] > 0.0


def test_exploration_usage(tmp_path):
    loop = _loop(tmp_path, epsilon=0.3, seed=11)
    loop.store.append_event(_event("top", "alpha policy", "Top pattern", 1.0, 1))
    loop.store.append_event(_event("mid", "alpha policy", "Mid pattern", 0.85, 2))
    loop.store.append_event(_event("low", "alpha policy", "Low pattern", 0.7, 3))

    exploration_traces = []
    for _ in range(30):
        loop.process("alpha policy")
        if loop.last_trace["exploration"]:
            exploration_traces.append(loop.last_trace)

    assert exploration_traces
    assert all(0.05 <= trace["exploration_rate"] <= 0.4 for trace in exploration_traces)
    assert any(trace["cluster_variance"] > 0.0 for trace in exploration_traces)
    assert any(len(set(trace["retrieved"])) > 1 for trace in exploration_traces)


def test_diversity_penalty(tmp_path):
    loop = _loop(tmp_path)
    loop.store.append_event(_event("top", "alpha policy", "Top output", 1.0, 1))
    loop.store.append_event(_event("alt", "alpha policy", "Alt output", 0.8, 2))

    assert loop.retrieval_set("alpha policy")[0]["id"] == "top"

    loop.store.record_usage(["top", "top", "top"])
    retrieved = loop.retrieval_set("alpha policy")

    assert retrieved[0]["id"] == "alt"
    assert retrieved[1]["id"] == "top"
    assert retrieved[1]["diversity_factor"] < retrieved[0]["diversity_factor"]


def test_temporal_credit(tmp_path):
    loop = _loop(tmp_path)
    early = _event(
        "early",
        "sequence step one",
        "Answer: gather context",
        0.8,
        1,
        future_score=0.7,
        expected_value=0.7,
        recent_scores=[0.7],
    )
    middle = _event(
        "middle",
        "sequence step two",
        "Answer: apply memory",
        0.7,
        2,
        future_score=0.6,
        expected_value=0.6,
        recent_scores=[0.6],
    )
    loop.store.append_event(early)
    loop.store.append_event(middle)

    loop.process("multi step sequence policy")
    state = loop.store.load_state()
    updates = {update["event_id"]: update for update in loop.last_trace["temporal_credit"]}

    assert state["early"]["future_score"] > 0.0
    assert updates["early"]["weight_after"] > updates["early"]["weight_before"]
    assert "middle" not in updates


def test_policy_stability_under_noise(tmp_path):
    loop = _loop(tmp_path, epsilon=0.2, seed=5)

    for _ in range(6):
        loop.process("create structured policy answer")

    for index in range(30):
        noisy = loop.store.create_event(
            f"noise sample {index}",
            f"Answer: random noise {index}",
            event_type="feedback",
        )
        loop.feedback(noisy.id, 1 if index % 2 == 0 else -1)
        loop.process("create structured policy answer")

    state = loop.store.load_state()
    policy = loop.store.load_policy_state()
    recent_scores = policy["evaluation_trend"][-10:]

    assert all(-1.0 <= event["weight"] <= 1.0 for event in state.values())
    assert 0.05 <= policy["exploration_rate"] <= 0.4
    assert policy["preferred_patterns"]
    assert max(recent_scores) - min(recent_scores) < 0.5


def test_multi_step_improvement(tmp_path):
    loop = _loop(tmp_path, epsilon=0.1, seed=3)
    scores = []

    for _ in range(12):
        loop.process("multi step sequence policy")
        scores.append(loop.last_trace["evaluation"])

    assert scores[-1] > scores[0]
    assert scores[-1] > 0.6
    assert min(scores[-3:]) >= scores[0]


def test_context_quality(tmp_path):
    loop = _loop(tmp_path, epsilon=0.1)
    loop.store.append_event(_event("top", "alpha policy", "Top policy output", 1.0, 1))
    loop.store.append_event(_event("mid", "alpha policy", "Mid policy output", 0.7, 2))
    loop.store.append_event(_event("low", "alpha policy", "Low policy output", 0.2, 3))
    loop.store.append_event(_event("noise", "banana recipe", "Noise output", 1.0, 4))

    output = loop.process("alpha policy")
    trace = loop.last_trace

    assert trace["retrieved"] == ["top", "mid", "low"]
    assert "[Relevant Prior Outputs]" in trace["context"]
    assert "[Current Task]\nalpha policy" in trace["context"]
    assert "Noise output" not in trace["context"]
    assert output.startswith("Task: alpha policy")


def test_system_memories_are_ignored_in_retrieval(tmp_path):
    loop = _loop(tmp_path)
    loop.store.append_event(
        _event("system", "alpha policy", "System instruction", 1.0, 1, event_type="system")
    )
    loop.store.append_event(
        _event("interaction", "alpha policy", "Interaction memory", 0.8, 2)
    )

    retrieved = loop.retrieval_set("alpha policy")

    assert [item["id"] for item in retrieved] == ["interaction"]


def test_suppression_robustness_after_decay(tmp_path):
    loop = _loop(tmp_path)
    bad = loop.store.create_event(
        "unsafe safety policy",
        "Answer: bad unsafe random noise",
    )
    for _ in range(8):
        loop.feedback(bad.id, -1)

    for _ in range(5):
        loop.process("safe policy guidelines")

    assert bad.id not in loop.last_trace["retrieved"]
    assert "bad unsafe random noise" not in loop.last_trace["output"]


def test_diversity_preservation_keeps_multiple_clusters_active(tmp_path):
    loop = _loop(tmp_path, epsilon=0.2, seed=9)
    loop.store.append_event(_event("policy", "alpha policy", "Policy pattern", 0.8, 1))
    loop.store.append_event(_event("solar", "solar roadmap", "Solar pattern", 0.8, 2))

    for index in range(24):
        task = "alpha policy" if index % 2 == 0 else "solar roadmap"
        loop.process(task)

    clusters = loop.store.load_clusters()
    active_clusters = [
        cluster
        for cluster in clusters.values()
        if cluster["shared_weight"] > loop.threshold and cluster["event_ids"]
    ]

    assert len(active_clusters) >= 2
    assert all(cluster["representative_id"] for cluster in active_clusters)


def test_phase3_trace_observability(tmp_path):
    loop = _loop(tmp_path, epsilon=0.2, seed=4)
    loop.process("observable memory policy")
    loop.process("observable memory policy")
    trace = loop.inspect_traces()[-1]

    required = {
        "input",
        "retrieved",
        "scores",
        "weights_before",
        "weights_after",
        "evaluation",
        "output",
        "exploration",
        "epsilon",
        "diversity_scores",
        "evaluation_components",
        "confidence",
        "policy_state_snapshot",
    }

    assert required.issubset(trace)
    assert trace["retrieved"]
    assert len(trace["retrieved"]) == len(trace["scores"]) == len(trace["weights_before"])
    assert set(trace["evaluation_components"]) == {"relevance", "coherence", "usefulness"}
    assert 0.0 <= trace["confidence"] <= 1.0


def test_bounded_behavior_after_twenty_cycles(tmp_path):
    loop = _loop(tmp_path)

    for _ in range(25):
        loop.process("stabilize structured policy answer")

    weights = [event["weight"] for event in loop.store.load_state().values()]

    assert weights
    assert all(-1.0 <= weight <= 1.0 for weight in weights)
    assert len(loop.inspect_traces()[-1]["output"]) < 1000


def test_counterfactual_delta(tmp_path):
    loop = _loop(tmp_path)
    critical = _event(
        "critical",
        "alpha policy",
        "Answer: clear structured specific actionable alpha policy",
        1.0,
        1,
        future_score=0.9,
        expected_value=0.9,
        recent_scores=[0.9, 0.8],
    )
    redundant = _event(
        "redundant",
        "alpha policy",
        "Answer: clear alpha",
        0.2,
        2,
        future_score=0.1,
        expected_value=0.2,
        recent_scores=[0.1],
    )
    loop.store.append_event(critical)
    loop.store.append_event(redundant)
    memories = loop.retrieval_set("alpha policy")

    critical_cf = counterfactual_evaluate("alpha policy", memories, "critical")
    redundant_cf = counterfactual_evaluate("alpha policy", memories, "redundant")

    assert critical_cf["delta"] > 0.0
    assert critical_cf["counterfactual_score"] < critical_cf["baseline_score"]
    assert critical_cf["delta"] > redundant_cf["delta"]


def test_regret_selection(tmp_path):
    loop = _loop(tmp_path, seed=2)
    high_risk = _event(
        "high_risk",
        "alpha policy",
        "Answer: volatile high value",
        1.0,
        1,
        future_score=1.0,
        expected_value=1.0,
        recent_scores=[1.0, -1.0, 1.0, -1.0],
    )
    stable = _event(
        "stable",
        "solar roadmap",
        "Answer: stable structured roadmap",
        0.8,
        2,
        future_score=0.75,
        expected_value=0.75,
        recent_scores=[0.72, 0.74, 0.76],
    )
    loop.store.append_event(high_risk)
    loop.store.append_event(stable)

    loop.process("alpha solar policy roadmap")
    trace = loop.last_trace
    selected_result = next(
        result
        for result in trace["strategy_results"]
        if result["strategy"]["cluster_id"] == trace["strategy_selected"]
    )

    assert selected_result["regret"] == min(trace["regret_values"])
    assert selected_result["selection_score"] == max(
        result["selection_score"] for result in trace["strategy_results"]
    )


def test_causal_attribution_precision(tmp_path):
    loop = _loop(tmp_path)
    high = _event(
        "high_effect",
        "alpha policy",
        "Answer: clear structured specific alpha policy",
        1.0,
        1,
        future_score=0.9,
        expected_value=0.9,
        recent_scores=[0.9],
    )
    low = _event(
        "low_effect",
        "alpha policy",
        "Answer: minor alpha note",
        0.2,
        2,
        future_score=0.1,
        expected_value=0.1,
        recent_scores=[0.1],
    )
    loop.store.append_event(high)
    loop.store.append_event(low)

    before = {event_id: payload["weight"] for event_id, payload in loop.store.load_state().items()}
    loop.process("alpha policy")
    state = loop.store.load_state()

    high_gain = state["high_effect"]["weight"] - before["high_effect"]
    low_gain = state["low_effect"]["weight"] - before["low_effect"]
    assert state["high_effect"]["marginal_effect"] > state["low_effect"]["marginal_effect"]
    assert state["high_effect"]["sensitivity_score"] > state["low_effect"]["sensitivity_score"]
    assert high_gain > low_gain


def test_policy_stability_tracking(tmp_path):
    loop = _loop(tmp_path, seed=7)
    loop.store.append_event(
        _event("alpha", "alpha policy", "Alpha policy", 0.8, 1, future_score=0.8, recent_scores=[0.8])
    )
    loop.store.append_event(
        _event("solar", "solar roadmap", "Solar roadmap", 0.8, 2, future_score=0.8, recent_scores=[0.8])
    )

    for index in range(16):
        task = "alpha policy" if index % 2 == 0 else "solar roadmap"
        loop.process(task)

    assert loop.last_trace["policy_stability"] > 0.0
    assert len({trace["strategy_selected"] for trace in loop.inspect_traces()[-8:]}) >= 2


def test_strategy_convergence(tmp_path):
    loop = _loop(tmp_path, seed=13)
    loop.store.append_event(
        _event(
            "winner",
            "alpha policy",
            "Answer: clear structured specific actionable policy",
            1.0,
            1,
            future_score=0.9,
            expected_value=0.9,
            recent_scores=[0.9, 0.85],
        )
    )
    loop.store.append_event(
        _event(
            "runner_up",
            "solar roadmap",
            "Answer: decent roadmap",
            0.5,
            2,
            future_score=0.3,
            expected_value=0.35,
            recent_scores=[0.3, 0.35],
        )
    )

    selected = []
    for _ in range(15):
        loop.process("alpha policy")
        selected.append(loop.last_trace["strategy_selected"])

    assert len(set(selected[-6:])) == 1
    assert selected[-1] == loop.store.load_state()["winner"]["cluster_id"]


def test_meta_prediction_correction(tmp_path):
    loop = _loop(tmp_path, seed=21)
    errors = []
    calibration = []

    for _ in range(12):
        loop.process("create structured policy answer")
        errors.append(loop.last_trace["prediction_error"])
        calibration.append(loop.last_trace["self_model_snapshot"]["calibration_error"])

    assert calibration[-1] <= max(calibration[:3])
    assert loop.last_trace["meta_score"] <= 1.0
    assert "prediction_bias" in loop.last_trace["self_model_snapshot"]


def test_model_selection_effectiveness(tmp_path):
    loop = _loop(tmp_path)
    loop.store.append_event(
        _event(
            "model_memory",
            "alpha policy",
            "Answer: clear structured policy",
            0.9,
            1,
            future_score=0.8,
            expected_value=0.8,
            recent_scores=[0.8],
        )
    )
    candidates = loop.retrieval_set("alpha policy")
    strategies = group_by_cluster([], {})
    # Build a direct strategy payload so model ranking can be tested without
    # depending on retrieval internals.
    strategies = [{"cluster_id": "manual", "memories": candidates, "variance": 0.0}]
    self_model = loop.store.load_self_model()
    self_model["model_rankings"]["baseline"] = 0.1
    self_model["model_rankings"]["high_sensitivity"] = 0.9

    rankings = rank_model_variants("alpha policy", strategies, self_model)
    selected = select_best_model(self_model, "alpha policy", strategies)
    good = evaluate_model_quality({"predicted_score": 0.7}, {"score": 0.7}, {})
    bad = evaluate_model_quality({"predicted_score": -0.2}, {"score": 0.7}, {})

    assert selected == "baseline"
    assert rankings["baseline"] < rankings["high_sensitivity"]
    assert good["overall_meta_score"] < bad["overall_meta_score"]


def test_calibration_convergence(tmp_path):
    loop = _loop(tmp_path)
    store = loop.store.load_store()
    store["self_model"]["prediction_bias"] = 0.6
    loop.store._write_store(store)

    before = loop.store.load_self_model()["prediction_bias"]
    for _ in range(8):
        loop.store.update_self_model(
            {
                "prediction_error": 0.0,
                "regret_error": 0.0,
                "attribution_error": 0.0,
                "overall_meta_score": 0.0,
            },
            "baseline",
            1.0,
        )
    after = loop.store.load_self_model()["prediction_bias"]

    assert after < before
    assert abs(after) < 0.05


def test_drift_detection(tmp_path):
    loop = _loop(tmp_path)

    drifted = loop.store.update_self_model(
        {
            "prediction_error": 0.8,
            "regret_error": 0.0,
            "attribution_error": 0.0,
            "overall_meta_score": 0.8,
        },
        "baseline",
        1.0,
    )
    for _ in range(6):
        corrected = loop.store.update_self_model(
            {
                "prediction_error": 0.0,
                "regret_error": 0.0,
                "attribution_error": 0.0,
                "overall_meta_score": 0.0,
            },
            "baseline",
            1.0,
        )

    assert drifted["confidence_drift"] > 0.0
    assert corrected["confidence_drift"] < drifted["confidence_drift"]


def test_meta_counterfactual_sensitivity(tmp_path):
    loop = _loop(tmp_path)
    loop.store.append_event(
        _event(
            "variant_memory",
            "alpha policy",
            "Answer: clear structured policy",
            1.0,
            1,
            future_score=0.8,
            expected_value=0.8,
            recent_scores=[0.8],
        )
    )
    candidates = loop.retrieval_set("alpha policy")
    strategy = {"cluster_id": "manual", "memories": candidates, "variance": 0.0}

    baseline = counterfactual_model_variation("alpha policy", "baseline", strategy)
    sensitive = counterfactual_model_variation("alpha policy", "high_sensitivity", strategy)

    assert sensitive != baseline
    assert sensitive > baseline
```
