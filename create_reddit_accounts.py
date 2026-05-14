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
