# 🚀 VPS Deployment Guide

Deploy the Hyperliquid AI Trading Agent to a VPS for 24/7 autonomous trading.

## Prerequisites

- Ubuntu 22.04+ VPS (recommended: 2GB RAM, 1 vCPU minimum)
- SSH access to your VPS
- Python 3.11+
- Your Hyperliquid API credentials

---

## Quick Start (5 minutes)

```bash
# 1. SSH into your VPS
ssh root@your-vps-ip

# 2. Clone the repo
git clone https://github.com/your-repo/hyperliquid-mcp-agent.git
cd hyperliquid-mcp-agent

# 3. Run the setup script
chmod +x scripts/setup.sh
./scripts/setup.sh

# 4. Configure environment
cp .env.example .env
nano .env  # Add your API keys

# 5. Start the agent
./scripts/start.sh
```

---

## Detailed Setup

### 1. System Preparation

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.11
sudo apt install -y python3.11 python3.11-venv python3-pip git tmux

# Install Node.js (for Streamlit dashboard)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

### 2. Project Setup

```bash
# Clone repository
git clone https://github.com/your-repo/hyperliquid-mcp-agent.git
cd hyperliquid-mcp-agent

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration

Create `.env` file with your credentials:

```bash
# .env
# ===== HYPERLIQUID CREDENTIALS =====
HL_PK=your_private_key_here
HL_WL=your_wallet_address_here

# ===== LLM API KEYS =====
OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
# OR
OPENAI_API_KEY=your_openai_key

# ===== AGENT CONFIG =====
ANALYST_MODEL=deepseek/deepseek-chat
RISK_MODEL=deepseek/deepseek-chat
INFERENCE_INTERVAL=180
USE_V2_ANALYST=1
USE_V2_RISK=1
FOCUS_COIN=BTC

# ===== OPTIONAL: TELEGRAM ALERTS =====
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 4. Running the Agent

#### Option A: Using tmux (Recommended)

```bash
# Start a tmux session
tmux new -s agent

# Activate venv and start MCP server
source venv/bin/activate
python deployment-test/server.py &

# Start the agent
python -m agent.main

# Detach from tmux: Ctrl+B, then D
# Reattach later: tmux attach -t agent
```

#### Option B: Using systemd (Production)

Create systemd service files:

```bash
# /etc/systemd/system/hl-mcp.service
sudo nano /etc/systemd/system/hl-mcp.service
```

```ini
[Unit]
Description=Hyperliquid MCP Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/hyperliquid-mcp-agent
Environment=PATH=/root/hyperliquid-mcp-agent/venv/bin
ExecStart=/root/hyperliquid-mcp-agent/venv/bin/python deployment-test/server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# /etc/systemd/system/hl-agent.service
sudo nano /etc/systemd/system/hl-agent.service
```

```ini
[Unit]
Description=Hyperliquid Trading Agent
After=hl-mcp.service
Requires=hl-mcp.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/hyperliquid-mcp-agent
Environment=PATH=/root/hyperliquid-mcp-agent/venv/bin
ExecStart=/root/hyperliquid-mcp-agent/venv/bin/python -m agent.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start services:

```bash
sudo systemctl daemon-reload
sudo systemctl enable hl-mcp hl-agent
sudo systemctl start hl-mcp
sudo systemctl start hl-agent

# Check status
sudo systemctl status hl-agent
```

### 5. Dashboard Access (Optional)

Run Streamlit dashboard on VPS:

```bash
# In tmux or as service
streamlit run ui/dashboard.py --server.port 8501 --server.address 0.0.0.0
```

Access via: `http://your-vps-ip:8501`

> ⚠️ **Security**: Use SSH tunnel for dashboard access instead of exposing port:
>
> ```bash
> ssh -L 8501:localhost:8501 root@your-vps-ip
> # Then access: http://localhost:8501
> ```

---

## Monitoring & Logs

```bash
# View agent logs (systemd)
sudo journalctl -u hl-agent -f

# View MCP server logs
sudo journalctl -u hl-mcp -f

# View database trades
sqlite3 agent.db "SELECT * FROM trades ORDER BY opened_at DESC LIMIT 10;"
```

---

## Security Checklist

- [ ] Use non-root user for running the agent
- [ ] Set up UFW firewall (allow only SSH)
- [ ] Use SSH keys instead of passwords
- [ ] Keep `.env` file secure (chmod 600)
- [ ] Regular backups of `agent.db`

```bash
# Firewall setup
sudo ufw allow ssh
sudo ufw enable
```

---

## Troubleshooting

| Issue                       | Solution                                                         |
| --------------------------- | ---------------------------------------------------------------- |
| Agent not connecting to MCP | Check if MCP server is running: `curl http://localhost:8000/sse` |
| LLM errors                  | Verify API keys in `.env`, check rate limits                     |
| Trade not executing         | Check MCP server logs for API errors                             |
| High memory usage           | Reduce `INFERENCE_INTERVAL` or restart agent                     |

---

## Backup & Recovery

```bash
# Backup database
cp agent.db agent.db.backup.$(date +%Y%m%d)

# Backup entire project
tar -czf hl-agent-backup.tar.gz hyperliquid-mcp-agent/
```

---

## Updating

```bash
cd hyperliquid-mcp-agent
git pull origin main
pip install -r requirements.txt
sudo systemctl restart hl-agent hl-mcp
```
