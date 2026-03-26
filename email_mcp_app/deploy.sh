#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy.sh  —  Full AWS EKS + RDS deployment for email-mcp-app
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

AWS_ACCOUNT="217140917846"
AWS_REGION="eu-west-1"
CLUSTER_NAME="email-mcp-cluster"
ECR_URI="${AWS_ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com/email-mcp-app"
IMAGE_TAG="${1:-latest}"
DB_IDENTIFIER="email-mcp-db"
DB_SUBNET_GROUP="email-mcp-db-subnet-group"
DB_SG_NAME="email-mcp-rds-sg"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Email MCP App — EKS + RDS Deployment"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Step 1: Install eksctl if missing ────────────────────────────────────────
if ! command -v eksctl &>/dev/null; then
  echo "[1/8] Installing eksctl..."
  curl -sLO "https://github.com/eksctl-io/eksctl/releases/latest/download/eksctl_Windows_amd64.zip"
  unzip -o eksctl_Windows_amd64.zip
  mkdir -p "$HOME/bin"
  mv eksctl.exe "$HOME/bin/eksctl"
  chmod +x "$HOME/bin/eksctl"
  rm eksctl_Windows_amd64.zip
else
  echo "[1/8] eksctl already installed — $(eksctl version)"
fi

# ── Step 2: Create EKS cluster ───────────────────────────────────────────────
if ! aws eks describe-cluster --name "$CLUSTER_NAME" --region "$AWS_REGION" &>/dev/null; then
  echo "[2/8] Creating EKS cluster: $CLUSTER_NAME (~15-20 min)..."
  eksctl create cluster \
    --name "$CLUSTER_NAME" \
    --region "$AWS_REGION" \
    --version 1.32 \
    --nodegroup-name standard-nodes \
    --node-type t3.medium \
    --nodes 2 \
    --nodes-min 2 \
    --nodes-max 10 \
    --managed \
    --asg-access \
    --full-ecr-access
else
  echo "[2/8] EKS cluster '$CLUSTER_NAME' already exists — skipping"
fi

# ── Step 3: Extract VPC/networking values ─────────────────────────────────────
echo "[3/8] Extracting VPC networking values..."
VPC_ID=$(aws eks describe-cluster \
  --name "$CLUSTER_NAME" --region "$AWS_REGION" \
  --query "cluster.resourcesVpcConfig.vpcId" --output text)

SUBNET_IDS=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=$VPC_ID" \
            "Name=tag:alpha.eksctl.io/cluster-name,Values=$CLUSTER_NAME" \
  --query "Subnets[?MapPublicIpOnLaunch==\`false\`].SubnetId" \
  --output text | tr '\t' ' ')

NODE_SG=$(aws ec2 describe-security-groups \
  --filters "Name=vpc-id,Values=$VPC_ID" \
            "Name=tag:alpha.eksctl.io/nodegroup-name,Values=standard-nodes" \
  --query "SecurityGroups[0].GroupId" --output text)

echo "  VPC: $VPC_ID  NodeSG: $NODE_SG"

# ── Step 4: Create RDS security group + subnet group ─────────────────────────
echo "[4/8] Setting up RDS networking..."

# Security group (idempotent)
if ! aws ec2 describe-security-groups \
     --filters "Name=group-name,Values=$DB_SG_NAME" "Name=vpc-id,Values=$VPC_ID" \
     --region "$AWS_REGION" --query "SecurityGroups[0].GroupId" --output text 2>/dev/null | grep -q "sg-"; then
  RDS_SG=$(aws ec2 create-security-group \
    --group-name "$DB_SG_NAME" \
    --description "RDS PostgreSQL access from EKS nodes" \
    --vpc-id "$VPC_ID" --region "$AWS_REGION" \
    --query "GroupId" --output text)
  aws ec2 authorize-security-group-ingress \
    --group-id "$RDS_SG" --protocol tcp --port 5432 \
    --source-group "$NODE_SG" --region "$AWS_REGION"
else
  RDS_SG=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=$DB_SG_NAME" "Name=vpc-id,Values=$VPC_ID" \
    --region "$AWS_REGION" --query "SecurityGroups[0].GroupId" --output text)
fi
echo "  RDS SG: $RDS_SG"

# Subnet group (idempotent)
if ! aws rds describe-db-subnet-groups \
     --db-subnet-group-name "$DB_SUBNET_GROUP" --region "$AWS_REGION" &>/dev/null; then
  aws rds create-db-subnet-group \
    --db-subnet-group-name "$DB_SUBNET_GROUP" \
    --db-subnet-group-description "Private subnets for email-mcp RDS" \
    --subnet-ids $SUBNET_IDS \
    --region "$AWS_REGION"
fi

# ── Step 5: Create RDS instance ───────────────────────────────────────────────
echo "[5/8] Provisioning RDS PostgreSQL (~5-10 min)..."
if ! aws rds describe-db-instances \
     --db-instance-identifier "$DB_IDENTIFIER" --region "$AWS_REGION" &>/dev/null; then
  aws rds create-db-instance \
    --db-instance-identifier "$DB_IDENTIFIER" \
    --db-instance-class db.t3.micro \
    --engine postgres \
    --engine-version 15.4 \
    --master-username postgres \
    --master-user-password 199103 \
    --db-name postgres \
    --vpc-security-group-ids "$RDS_SG" \
    --db-subnet-group-name "$DB_SUBNET_GROUP" \
    --no-publicly-accessible \
    --storage-type gp2 \
    --allocated-storage 20 \
    --backup-retention-period 7 \
    --no-multi-az \
    --region "$AWS_REGION"

  echo "  Waiting for RDS to become available..."
  aws rds wait db-instance-available \
    --db-instance-identifier "$DB_IDENTIFIER" --region "$AWS_REGION"
else
  echo "  RDS instance already exists — skipping creation"
fi

RDS_ENDPOINT=$(aws rds describe-db-instances \
  --db-instance-identifier "$DB_IDENTIFIER" --region "$AWS_REGION" \
  --query "DBInstances[0].Endpoint.Address" --output text)
echo "  RDS endpoint: $RDS_ENDPOINT"

# ── Step 6: Update secret.yaml with real RDS endpoint ────────────────────────
echo "[6/8] Updating k8s/secret.yaml with RDS endpoint..."
sed -i "s|DATABASE_URL:.*|DATABASE_URL: \"postgresql://postgres:199103@${RDS_ENDPOINT}:5432/postgres\"|" k8s/secret.yaml

# ── Step 7: Update kubeconfig + Deploy manifests ──────────────────────────────
echo "[7/8] Deploying to EKS..."
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$AWS_REGION"

# metrics-server (required for HPA)
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# Apply manifests in dependency order
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/pvc.yaml
kubectl wait --for=condition=Bound pvc/gmail-token-pvc -n email-mcp --timeout=120s
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/hpa.yaml

# Initialize DB schema from inside the cluster (RDS is not publicly accessible)
echo "  Initialising database schema..."
kubectl run db-init \
  --image="${ECR_URI}:${IMAGE_TAG}" \
  --namespace=email-mcp --restart=Never \
  --env="DATABASE_URL=postgresql://postgres:199103@${RDS_ENDPOINT}:5432/postgres" \
  --command -- python setup_db.py
kubectl wait --for=condition=Completed pod/db-init -n email-mcp --timeout=120s
kubectl logs db-init -n email-mcp
kubectl delete pod db-init -n email-mcp

# ── Step 8: CloudWatch monitoring ────────────────────────────────────────────
echo "[8/8] Enabling CloudWatch Container Insights..."

# Attach CloudWatch policy to node role
NODE_ROLE=$(aws iam list-roles \
  --query "Roles[?contains(RoleName,'NodeInstanceRole')&&contains(RoleName,'email-mcp')].RoleName" \
  --output text)
aws iam attach-role-policy \
  --role-name "$NODE_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy 2>/dev/null || true

kubectl apply -f k8s/monitoring/cloudwatch-namespace.yaml
kubectl apply -f k8s/monitoring/cloudwatch-agent-configmap.yaml

helm repo add aws-cloudwatch-metrics https://aws.github.io/eks-charts 2>/dev/null || true
helm repo update
helm upgrade --install aws-cloudwatch-metrics \
  aws-cloudwatch-metrics/aws-cloudwatch-metrics \
  --namespace amazon-cloudwatch \
  --set clusterName="$CLUSTER_NAME"

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Deployment complete!"
echo " ECR image   : ${ECR_URI}:${IMAGE_TAG}"
echo " Cluster     : ${CLUSTER_NAME} (${AWS_REGION})"
echo " RDS endpoint: ${RDS_ENDPOINT}"
echo " Namespace   : email-mcp"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo " Verify with:"
echo "   kubectl get nodes"
echo "   kubectl get all -n email-mcp"
echo "   kubectl get hpa -n email-mcp"
echo "   kubectl top pods -n email-mcp"
