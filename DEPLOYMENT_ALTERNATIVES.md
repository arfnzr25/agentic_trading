# 🚀 Alternative Deployment Options

In addition to the manual setup described in `DEPLOYMENT.md`, we provide two "Quick Deploy" alternatives:

1. **Docker Compose (Recommended)**: Best for reliability, isolation, and quick updates.
2. **One-Click Script**: Best for bare-metal VPS if you prefer not to use Docker.

> [!IMPORTANT] > **New Requirement**: The agent now features a DSPy Shadow Mode.
> Ensure your `.env` file includes `OPENROUTER_BASE_URL` and `OPENROUTER_API_KEY` for it to function.

---

## Option 1: Docker Compose (Speed & Isolation)

This method runs the MCP Server, the Trading Agent (including Shadow Simulator), and the Dashboard as managed containers.

### Prerequisites

- Docker & Docker Compose installed on your VPS. (See [Appendix](#appendix-installing-docker-on-ubuntu-linux-vps))

### Steps

1. **Clone & Configure**

   ```bash
   git clone https://github.com/your-repo/hyperliquid-mcp-agent.git
   cd hyperliquid-mcp-agent
   cp .env.example .env
   nano .env  # Update keys (Hyperliquid, OpenRouter, Telegram)
   ```

2. **Start the Agent (Headless)**

   ```bash
   docker compose up -d
   ```

   _This starts the MCP Server and Trading Agent in the background. The Shadow Database (`dspy_memory.db`) is automatically persisted in the `agent_data` volume._

3. **Secure Dashboard Access (Manual Intervention)**

   The dashboard is not exposed publicly by default. To access it securely:

   **Step A: Start the Dashboard Container**

   ```bash
   docker compose --profile dashboard up -d
   ```

   **Step B: Create an SSH Tunnel**
   On your **local machine** (not the VPS), run:

   ```bash
   # Syntax: ssh -L local_port:localhost:remote_port user@vps_ip
   ssh -L 8501:localhost:8501 root@your-vps-ip
   ```

   **Step C: Access Locally**
   Open your browser and visit: `http://localhost:8501`

   _This method encrypts all traffic and requires no open ports on the VPS firewall._

### Stopping/Updating

- Stop core services: `docker compose down`
- Stop dashboard only: `docker compose --profile dashboard stop`
- Update: `git pull && docker compose up -d --build`

### Monitoring & Debugging (Live State)

To see verbose information about the state of your containers live on the VPS:

1.  **View Live Logs** (Stream logs from all services):

    ```bash
    docker compose logs -f
    ```

    - To view shadow mode logs specifically:
      ```bash
      docker compose logs -f agent | grep "Shadow Mode"
      ```

2.  **Check Process Status**:

    ```bash
    docker compose ps
    ```

---

## Option 2: One-Click VPS Script (Simple)

This script automates the "Detailed Setup" from the main guide. It installs Python, Node.js, sets up the virtual environment, and creates systemd services.

### Steps

1. **Upload & Run**

   ```bash
   # Download the script (or create it)
   curl -O https://raw.githubusercontent.com/your-repo/hyperliquid-mcp-agent/main/scripts/setup_vps.sh

   # Make executable
   chmod +x setup_vps.sh

   # Run
   ./setup_vps.sh
   ```

2. **Configure**
   The script will pause to let you edit `.env`. **Make sure to fill in the Notification sections if you want Telegram alerts.**

3. **Verify**
   ```bash
   sudo systemctl status hl-agent
   ```

---

## Comparison

| Feature        | Docker Compose              | One-Click Script               | Manual Setup |
| -------------- | --------------------------- | ------------------------------ | ------------ |
| **Setup Time** | ~2 mins                     | ~5 mins                        | ~15 mins     |
| **Updates**    | `docker compose up --build` | `git pull && restart services` | Manual       |
| **Isolation**  | High (Containers)           | Low (System Python)            | Low          |
| **Complexity** | Low                         | Low                            | High         |

---

## Appendix: Installing Docker on Ubuntu (Linux VPS)

If your VPS does not have Docker installed, follow these commands:

1.  **Set up the repository**

    ```bash
    # Update package index and install prerequisite packages
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl gnupg

    # Create keyrings directory
    sudo install -m 0755 -d /etc/apt/keyrings

    # Download Docker's official GPG key
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    # Set up the repository
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    ```

2.  **Install Docker Engine**

    ```bash
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    ```

3.  **Verify Installation**

    ```bash
    sudo docker run hello-world
    ```

4.  **(Optional) Run Docker without sudo**
    ```bash
    sudo groupadd docker
    sudo usermod -aG docker $USER
    newgrp docker
    ```
