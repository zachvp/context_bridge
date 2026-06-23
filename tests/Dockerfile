FROM python:3.13-slim

# System deps + Claude Code apt repo
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates shellcheck \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://downloads.claude.ai/keys/claude-code.asc \
         -o /etc/apt/keyrings/claude-code.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/claude-code.asc] https://downloads.claude.ai/claude-code/apt/stable stable main" \
         > /etc/apt/sources.list.d/claude-code.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends claude-code \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /context_bridge
COPY . .

RUN pip install --no-cache-dir -r requirements.txt

ENV PYTHONPATH=/context_bridge

CMD ["bash", "tests/test_install.sh"]
