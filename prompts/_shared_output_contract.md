# Output envelope (all agents)

The application **parses your reply as JSON**. It does not use tool calls for this step.

- Your **entire** assistant message must be **one JSON object** only.
- **No** preamble or postscript outside that object (no “Here is the JSON:”, no markdown wrapping the object unless the parser strips it—prefer raw JSON only).
- Top-level keys are always **`discord_markdown`** (string) and **`structured`** (object). Agent-specific fields live **inside** `structured` as documented in the agent schema below.
- The app posts **`discord_markdown` verbatim** to Discord. Put **only** human-readable markdown in that string (tables, bullets, bold). Do **not** put the outer JSON object, JSON code fences, or schema key names into that field.
