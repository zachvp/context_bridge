# Security Policy

## Reporting a vulnerability
If you find a security issue in Context Bridge, please **do not open a public
GitHub issue**. Instead, email **dev@solreason.xyz** with:

- A description of the vulnerability and its potential impact
- Steps to reproduce or proof-of-concept (if applicable)
- Any suggested fix, if you have one

## Scope
Context Bridge is a local tool. The MCP server runs on your own machine and
communicates only over stdio with Claude Code. It does not make outbound
network requests at runtime (model weights are downloaded once from HuggingFace
during setup).

The main assets are:
- `chat_memory.db`: contains embeddings of your Claude conversation history;
  treat it as sensitive personal data and restrict file permissions accordingly
- The MCP `stdio` channel: runs as your local user, no authentication layer

## Security notes for users
- `chat_memory.db` and `data/` are gitignored by default. Avoid committing them.
- The `.env` file is also gitignored. Avoid committing it.
- If you share your machine, set restrictive permissions on the database:
  `chmod 600 chat_memory.db`
