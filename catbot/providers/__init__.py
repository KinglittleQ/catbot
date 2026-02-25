"""
catbot — A minimal Python agent framework with Feishu support.

A clean Python implementation of the openclaw agent architecture:
- Agent loop (think → tool call → observe → repeat)
- OpenClaw-style session keys (agent:<id>:<channel>:<type>:<chat_id>)
- Compaction: summarize old messages to stay within context window
- Memory: MEMORY.md (long-term facts) + HISTORY.md (event log)
- Feishu WebSocket channel with @ detection and emoji reactions
- Middleware chain: rate limiting, allowlists, logging
- Multi-provider: OpenAI-compatible + Anthropic (with prompt caching)
"""
