# Security Policy

## Reporting a vulnerability

If you find a security issue in Context Bridge, please **do not open a public
GitHub issue**. Instead, email **vegaperk@gmail.com** with:

- A description of the vulnerability and its potential impact
- Steps to reproduce or proof-of-concept (if applicable)
- Any suggested fix, if you have one

You should receive a response within 7 days. Please allow reasonable time for
a fix to be developed before public disclosure.

## Scope

Context Bridge is a local tool — the MCP server runs on your own machine and
communicates only over stdio with Claude Code. It does not make outbound
network requests at runtime (model weights are downloaded once from HuggingFace
during setup).

The main assets in scope are:

- `chat_memory.db` — contains embeddings of your Claude conversation history;
  treat it as sensitive personal data and restrict file permissions accordingly
- The MCP stdio channel — runs as your local user, no authentication layer

## Security notes for users

- `chat_memory.db` and `data/` are gitignored by default. Do not commit them.
- The `.env` file is also gitignored. Do not commit it.
- If you share your machine, set restrictive permissions on the database:
  `chmod 600 chat_memory.db`
