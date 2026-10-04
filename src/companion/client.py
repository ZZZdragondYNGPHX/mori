"""Dependency-free IPC client usable from CLI, hooks, and external plugins."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from .paths import state_dir


def send_event(payload, adapter=None, descriptor_path=None, timeout=0.35):
    descriptor_path = Path(descriptor_path) if descriptor_path else state_dir() / "bridge.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    url = descriptor["url"]
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or
            parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Bridge descriptor must use a loopback HTTP URL")
    endpoint = "/v1/events" if adapter is None else "/v1/adapters/" + adapter
    if adapter is None and payload.get("schema_version") == 2:
        endpoint = "/v2/notices"
    request = Request(url + endpoint, json.dumps(payload, ensure_ascii=False).encode("utf-8"), {
        "Authorization": "Bearer " + descriptor["token"],
        "Content-Type": "application/json",
    }, method="POST")
    # Local IPC must bypass corporate/system HTTP proxies.
    with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
        return json.load(response)
