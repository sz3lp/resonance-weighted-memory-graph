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
