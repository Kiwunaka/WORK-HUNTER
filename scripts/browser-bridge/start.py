"""Connect the official Playwright extension; keep its token in memory only."""
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".work-hunter" / "browser-bridge.json"


async def main() -> None:
    token = os.environ.get("PLAYWRIGHT_MCP_EXTENSION_TOKEN", "")
    if not token:
        if sys.stdin.isatty():
            token = getpass.getpass("Playwright extension token: ")
        else:
            print("Waiting for extension token on stdin (not saved).", flush=True)
            token = sys.stdin.readline().strip()
    if not token:
        raise ValueError("Extension token is required")
    env = {**os.environ, "PLAYWRIGHT_MCP_EXTENSION_TOKEN": token}
    params = StdioServerParameters(
        command="node",
        args=[str(Path(__file__).parent / "node_modules/playwright-core/cli.js"),
              "mcp", "--extension", "--browser", "chrome", "--snapshot-mode", "none"],
        env=env, cwd=ROOT,
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            code = (
                "async page => await page.context().browser().bind('Work Hunter', "
                + json.dumps({"workspaceDir": str(ROOT)}) + ")"
            )
            # Fixed local bootstrap code, never model- or webpage-provided code.
            result = await session.call_tool("browser_run_code_unsafe", {"code": code})
            if result.isError:
                # Extension errors can contain a connect URL carrying the token.
                messages = [getattr(block, "text", "") for block in result.content]
                raise RuntimeError(" ".join(messages).replace(token, "[redacted]"))
            endpoint = ""
            for block in result.content:
                text = getattr(block, "text", "")
                for line in text.splitlines():
                    if line.strip().startswith("{"):
                        try:
                            endpoint = json.loads(line).get("endpoint", "")
                        except (ValueError, AttributeError):
                            pass
                # MCP emits pretty-printed JSON under ### Result.
                if not endpoint and "### Result" in text:
                    fragment = text.split("### Result", 1)[1].lstrip()
                    try:
                        value, _ = json.JSONDecoder().raw_decode(fragment)
                        endpoint = value.get("endpoint", "")
                    except (ValueError, AttributeError):
                        pass
            if not endpoint:
                raise RuntimeError("Extension connected, but did not return a browser endpoint")
            STATE.write_text(json.dumps({
                "endpoint": endpoint, "pid": os.getpid(), "enabled": True,
                "sources": ["habr", "geekjob", "hirehi", "careerspace", "getmatch", "rvc"],
            }, ensure_ascii=False), encoding="utf-8")
            print("Work Hunter browser bridge connected. Token is memory-only.", flush=True)
            try:
                while True:
                    await asyncio.sleep(30)
                    await session.send_ping()
            finally:
                STATE.write_text(json.dumps({"enabled": True, "connected": False}), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
