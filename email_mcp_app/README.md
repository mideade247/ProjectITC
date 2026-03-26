# Email MCP App

A Claude-powered email assistant that connects Gmail and PostgreSQL via the **Model Context Protocol (MCP)**. Send emails, read your inbox, and log every email event to a database — all through natural language.

---

## Features

- **Send & Read Emails** via Gmail API
- **Log every email event** to PostgreSQL automatically
- **Search & query** email history with natural language
- **Claude AI** orchestrates all actions via MCP tools
- **Dockerised** — runs anywhere
- **Deployed on AWS EKS** with auto-scaling and CloudWatch monitoring

---

## Architecture

```
You (CLI)
    │
    ▼
app.py  ──── Claude claude-sonnet-4-6
    │               │
    ├── Gmail MCP Server (servers/gmail_server.py)
    │       └── Gmail API (send, list, read emails)
    │
    └── PostgreSQL MCP Server (servers/postgres_server.py)
            └── AWS RDS PostgreSQL (log, search, stats)
```

---

## Prerequisites

| Tool | Purpose |
|---|---|
| Python 3.11+ | Runtime |
| Docker | Container build & run |
| AWS CLI | ECR / EKS / RDS access |
| kubectl | Kubernetes management |
| eksctl | EKS cluster creation |
| Anthropic API Key | Claude AI |
| Google Cloud credentials | Gmail OAuth2 |

---

## Quick Start (Local)

### 1. Clone & install dependencies
```bash
git clone <repo-url>
cd email_mcp_app
python -m venv venv
source venv/Scripts/activate   # Windows
pip install -r requirements.txt
```

### 2. Set up environment variables
```bash
cp .env.example .env
```
Edit `.env` with your real values:
```env
ANTHROPIC_API_KEY=sk-ant-...
GMAIL_CREDENTIALS_PATH=credentials.json
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

### 3. Add Gmail credentials
Download `credentials.json` from [Google Cloud Console](https://console.cloud.google.com) → APIs & Services → Credentials and place it in the project root.

### 4. Initialise the database
```bash
python setup_db.py
```

### 5. Run the app
```bash
python app.py
```

**Example prompts:**
```
You: Send an email to alice@example.com about the project update
You: List my last 5 emails
You: Show email stats
You: Search emails about invoice
```

---

## Running with Docker

### Build
```bash
docker build -t email-mcp-app .
```

### Run
```bash
docker run -it \
  -v $(pwd)/credentials.json:/app/credentials.json:ro \
  -v $(pwd)/token.json:/app/token.json \
  --env-file .env \
  email-mcp-app
```

### Pull from Docker Hub
```bash
docker pull mideade/email-mcp-app:latest

docker run -it \
  -v $(pwd)/credentials.json:/app/credentials.json:ro \
  -v $(pwd)/token.json:/app/token.json \
  --env-file .env \
  mideade/email-mcp-app:latest
```

---

## AWS Deployment (EKS + RDS)

### Infrastructure Overview

| Resource | Detail |
|---|---|
| **ECR** | `217140917846.dkr.ecr.eu-west-1.amazonaws.com/email-mcp-app` |
| **EKS Cluster** | `email-mcp-cluster` — eu-west-1 — Kubernetes v1.32 |
| **Nodes** | t3.medium × 2 (auto-scales to 10) |
| **RDS** | `email-mcp-db.cxuwqoeswl7m.eu-west-1.rds.amazonaws.com` — PostgreSQL 15.17 |
| **Monitoring** | CloudWatch Container Insights |

---

### Push Image to ECR
```bash
aws ecr get-login-password --region eu-west-1 \
  | docker login --username AWS --password-stdin \
    217140917846.dkr.ecr.eu-west-1.amazonaws.com

docker tag mideade/email-mcp-app:latest \
  217140917846.dkr.ecr.eu-west-1.amazonaws.com/email-mcp-app:latest

docker push 217140917846.dkr.ecr.eu-west-1.amazonaws.com/email-mcp-app:latest
```

---

### Deploy to EKS (automated)
```bash
chmod +x deploy.sh
./deploy.sh
```

This script:
1. Installs `eksctl` if missing
2. Creates the EKS cluster
3. Provisions AWS RDS PostgreSQL in the same VPC
4. Updates `k8s/secret.yaml` with the RDS endpoint
5. Applies all Kubernetes manifests
6. Initialises the database schema
7. Deploys CloudWatch monitoring

---

### Deploy to EKS (manual steps)

**1. Create cluster**
```bash
eksctl create cluster \
  --name email-mcp-cluster \
  --region eu-west-1 \
  --version 1.32 \
  --nodegroup-name standard-nodes \
  --node-type t3.medium \
  --nodes 2 --nodes-min 2 --nodes-max 10 \
  --managed --asg-access --full-ecr-access
```

**2. Update kubeconfig**
```bash
aws eks update-kubeconfig --name email-mcp-cluster --region eu-west-1
```

**3. Apply manifests**
```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/pvc.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/hpa.yaml
```

**4. Add Gmail credentials secret**
```bash
kubectl create secret generic gmail-credentials-secret \
  --from-file=credentials.json=./credentials.json \
  --namespace=email-mcp
```

**5. Initialise DB schema**
```bash
kubectl run db-init \
  --image=217140917846.dkr.ecr.eu-west-1.amazonaws.com/email-mcp-app:latest \
  --namespace=email-mcp --restart=Never \
  --env="DATABASE_URL=<your-rds-url>" \
  --command -- python setup_db.py

kubectl logs db-init -n email-mcp --follow
kubectl delete pod db-init -n email-mcp
```

---

## Kubernetes Manifests

```
k8s/
├── namespace.yaml        # email-mcp namespace
├── secret.yaml           # API keys & DB URL (never commit with real values)
├── configmap.yaml        # Non-sensitive env vars
├── pvc.yaml              # 1Gi EBS volume for token.json persistence
├── deployment.yaml       # 2 replicas, rolling updates, health checks
├── service.yaml          # ClusterIP service
├── hpa.yaml              # Auto-scales 2→10 pods on CPU>60% or Memory>70%
└── monitoring/
    ├── cloudwatch-namespace.yaml
    └── cloudwatch-agent-configmap.yaml
```

---

## Scalability

Auto-scaling is managed by the **Horizontal Pod Autoscaler (HPA)**:

| Setting | Value |
|---|---|
| Min pods | 2 |
| Max pods | 10 |
| Scale up trigger | CPU > 60% or Memory > 70% |
| Scale up rate | Max 2 pods per minute |
| Scale down rate | Max 1 pod per 2 minutes |

**Check live scaling status:**
```bash
kubectl get hpa -n email-mcp --watch
```

---

## Monitoring

CloudWatch Container Insights is deployed as a DaemonSet across all nodes.

**View metrics:** AWS Console → CloudWatch → Container Insights → `email-mcp-cluster`

**Check agent status:**
```bash
kubectl get pods -n amazon-cloudwatch
```

---

## Testing the Deployment

```bash
# Nodes healthy
kubectl get nodes

# All resources running
kubectl get all -n email-mcp

# DB connection works from inside the cluster
kubectl exec -n email-mcp \
  $(kubectl get pod -n email-mcp -l app=email-mcp-app -o jsonpath='{.items[0].metadata.name}') \
  -- python -c "
import os, psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM email_logs')
print('DB OK — rows:', cur.fetchone()[0])
conn.close()
"

# App logs — confirms Gmail + PostgreSQL connected
kubectl logs -n email-mcp \
  $(kubectl get pod -n email-mcp -l app=email-mcp-app -o jsonpath='{.items[0].metadata.name}') \
  --previous

# RDS status
aws rds describe-db-instances \
  --db-instance-identifier email-mcp-db \
  --region eu-west-1 \
  --query "DBInstances[0].{Status:DBInstanceStatus,Endpoint:Endpoint.Address}" \
  --output table
```

---

## Project Structure

```
email_mcp_app/
├── app.py                 # Main Claude MCP client (CLI)
├── servers/
│   ├── gmail_server.py    # Gmail MCP server (send, list, read)
│   └── postgres_server.py # PostgreSQL MCP server (log, search, stats)
├── schema.sql             # Database schema
├── setup_db.py            # DB initialisation script
├── requirements.txt       # Python dependencies
├── Dockerfile             # Container definition
├── .dockerignore          # Excludes secrets from image
├── .gitignore             # Excludes secrets from git
├── .env.example           # Environment variable template
├── deploy.sh              # Full automated EKS deployment script
├── mcp_config.json        # Claude Desktop MCP config reference
└── k8s/                   # Kubernetes manifests
```

---

## Security Notes

- `.env`, `credentials.json`, `token.json` and `k8s/secret.yaml` are **gitignored** — never commit them
- Docker image contains **no secrets** — all injected at runtime via env vars or volume mounts
- RDS is **not publicly accessible** — reachable only from EKS nodes via VPC security group
- Pods run as **non-root user** (UID 1000)

---

## MCP Tools Available

| Tool | Server | Description |
|---|---|---|
| `send_email` | Gmail | Send an email |
| `list_emails` | Gmail | List recent inbox emails |
| `get_email` | Gmail | Read full email by ID |
| `get_my_email_address` | Gmail | Get authenticated Gmail address |
| `log_email` | PostgreSQL | Log an email event to DB |
| `get_email_logs` | PostgreSQL | Query logged emails |
| `get_email_stats` | PostgreSQL | Sent/received counts and trends |
| `search_emails_in_db` | PostgreSQL | Full-text search across logs |

---

## License

MIT
