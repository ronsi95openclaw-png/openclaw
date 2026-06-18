# MCP servers: Perplexity, Firecrawl, Chrome DevTools

Three MCP servers surfaced via "marketerhub.ai" follow-reels, requested for
install + test on 2026-06-17. All three are **genuine official repos** (verified
on GitHub — no typosquat) and all three pass the MCP `initialize` + `tools/list`
handshake when launched over stdio.

## Identity check + test results

| Server | Official repo | npm package | Key needed | Handshake | Tools |
|--------|---------------|-------------|-----------|-----------|-------|
| Chrome DevTools | `ChromeDevTools/chrome-devtools-mcp` (Google, ~44k★) | `chrome-devtools-mcp` | none | ✅ | 29 |
| Firecrawl | `firecrawl/firecrawl-mcp-server` (~6.6k★) | `firecrawl-mcp` | `FIRECRAWL_API_KEY` | ✅ | 21 |
| Perplexity | `perplexityai/modelcontextprotocol` (the reel's `ppl-ai/...` redirects here) | `@perplexity-ai/mcp-server` | `PERPLEXITY_API_KEY` | ✅ | 4 |

> ⚠️ **Reel sourcing.** Same "follow for the link" channel as the earlier batch —
> so each repo/owner was verified against GitHub before install. Note the reel
> labeled Perplexity's as `ppl-ai/modelcontextprotocol`; the canonical repo is
> `perplexityai/modelcontextprotocol` and the official npm scope is
> `@perplexity-ai`. Confirm the package scope when you install.

### What "tested" means here vs. what it doesn't

- ✅ **Verified:** each server installs from npm, launches over stdio, completes
  the MCP handshake, and returns its full tool list. That is the meaningful
  "the server works" check for an MCP server. (Tested with a tiny
  `@modelcontextprotocol/sdk` stdio client.)
- ⛔ **Not verifiable in the web sandbox:** actually *executing* a tool, because
  - Firecrawl/Perplexity need a **real API key** and outbound egress to
    `api.firecrawl.dev` / `api.perplexity.ai` (this sandbox's egress allowlist
    blocks them).
  - Chrome DevTools launches **Chrome** on the first tool call; the sandbox has
    no launchable browser. The server itself runs fine without it.

## Client config (Claude Code / Claude Desktop / Cursor)

Add to your MCP client config (e.g. `~/.claude.json` `mcpServers`, Claude
Desktop `claude_desktop_config.json`, or a project `.mcp.json`). Put real keys in
env — never commit them.

```jsonc
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": ["-y", "chrome-devtools-mcp@latest"]
      // requires Google Chrome (or Chrome for Testing) installed
    },
    "firecrawl": {
      "command": "npx",
      "args": ["-y", "firecrawl-mcp"],
      "env": { "FIRECRAWL_API_KEY": "fc-YOUR_KEY" }
    },
    "perplexity": {
      "command": "npx",
      "args": ["-y", "@perplexity-ai/mcp-server"],
      "env": { "PERPLEXITY_API_KEY": "pplx-YOUR_KEY" }
    }
  }
}
```

For Claude Code you can also add them via CLI, e.g.:

```bash
claude mcp add firecrawl --env FIRECRAWL_API_KEY=fc-YOUR_KEY -- npx -y firecrawl-mcp
claude mcp add perplexity --env PERPLEXITY_API_KEY=pplx-YOUR_KEY -- npx -y @perplexity-ai/mcp-server
claude mcp add chrome-devtools -- npx -y chrome-devtools-mcp@latest
```

## Reproduce the handshake test

```bash
mkdir mcp-test && cd mcp-test && npm init -y && npm pkg set type=module
npm install @modelcontextprotocol/sdk
# tester.js: connect via StdioClientTransport, call listTools(), print names
node tester.js npx -y chrome-devtools-mcp@latest
FIRECRAWL_API_KEY=fc-dummy   node tester.js npx -y firecrawl-mcp
PERPLEXITY_API_KEY=pplx-dummy node tester.js npx -y @perplexity-ai/mcp-server
```

(Dummy keys are enough to start Firecrawl/Perplexity and list tools; real keys +
network are only needed to *execute* a tool.)
