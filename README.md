# PSS Remediation Agent with kagent + kmcp

This project wires together:

- A **custom MCP server** that analyzes Kubernetes manifests against **Pod Security Standards** (Baseline / Restricted).
- A **kagent Agent** (`pss-remediator`) that:
  - Fetches **live** manifests from your cluster.
  - Sends them to the MCP server for PSS analysis.
  - Returns a list of violations and suggested patches.

Use this to help engineering teams quickly identify and remediate Pod Security Standards (PSS) misconfigurations in Kubernetes workloads.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)  
2. [Prerequisites](#prerequisites)  
   - [Install `kmcp`](#install-kmcp)  
   - [Install `kagent` CLI](#install-kagent-cli)  
3. [Install kagent](#install-kagent)  
   - [1. Install CRDs](#1-install-crds)  
   - [2. Configure an OpenAI API key](#2-configure-an-openai-api-key-or-other-provider)  
   - [3. Install kagent (Controller + UI)](#3-install-kagent-controller---ui)  
4. [Build & Deploy the PSS MCP Server](#build--deploy-the-pss-mcp-server)  
   - [1. Scaffold the Project](#1-scaffold-if-starting-from-scratch)  
   - [2. Implement the PSS Analysis Tool](#2-implement-the-pss-analysis-tool)  
   - [3. Build and Load the Image (Kind)](#3-build-and-load-the-image-kind)  
   - [4. Configure `kmcp.yaml` for kagent](#4-configure-kmcpyaml-for-kagent)  
   - [5. Deploy the MCP Server](#5-deploy-the-mcp-server)  
5. [Create the `pss-remediator` Agent](#create-the-pss-remediator-agent)  
6. [Demo: Scan a Deliberately Bad Deployment](#demo-scan-a-deliberately-bad-deployment)  
   - [1. Deploy a PSS-Violating Workload](#1-deploy-a-pss-violating-workload)  
   - [2. Launch kagent Dashboard](#2-launch-kagent-dashboard)  
7. [Troubleshooting](#troubleshooting)  
8. [Next Steps / Extensions](#next-steps--extensions)

---

## Architecture Overview

At a high level:

- **kagent** runs in the cluster and hosts:
  - A **controller** that manages Agents.
  - A **web UI / dashboard**.
  - Built-in **Remote MCP servers**, including `kagent-tool-server` for talking to the Kubernetes API.

- **PSS MCP server (FastMCP + kmcp)**:
  - A custom MCP server (Python) that exposes a tool like `analyze_manifest_for_pss`.
  - Takes YAML for Pods / Deployments / DaemonSets / etc.
  - Runs a PSS ruleset and returns structured JSON with violations + suggested patches.

- **`pss-remediator` Agent**:
  - Uses `kagent-tool-server` to fetch manifests from the cluster via `k8s_get_resources`.
  - Uses the PSS MCP server to analyze those manifests.
  - Summarizes results and suggests patches, via your LLM provider (e.g., OpenAI).

---

## Prerequisites

You’ll need:

- A Kubernetes cluster  
  - Local: Kind, Minikube  
  - Remote: GKE, AKS, EKS, etc.
- `kubectl` and `helm` installed and pointed at your cluster.
- `kmcp` CLI installed.
- `kagent` CLI installed.
- An LLM provider key with **API quota** (examples here use **OpenAI**).

### Install `kmcp`

```bash
curl -fsSL https://raw.githubusercontent.com/kagent-dev/kmcp/refs/heads/main/scripts/get-kmcp.sh | bash
```

### Install `kagent` CLI

```bash
curl -fsSL https://raw.githubusercontent.com/kagent-dev/kagent/refs/heads/main/scripts/get-kagent | bash
```

These scripts typically install binaries into `~/.local/bin` or similar; make sure that directory is on your `$PATH`.

---

## Install kagent

`kagent` consists of:

- A **CRD chart** (`kagent-crds`) – must be installed first.  
- The main **kagent chart** (`kagent`) – controller, UI, built-in `RemoteMCPServer`s, etc.

### 1. Install CRDs

Set the kagent version you want (example: **0.7.4**):

```bash
export KAGENT_VERSION=0.7.4
```

Install the CRDs:

```bash
helm install kagent-crds oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds \
  --namespace kagent \
  --create-namespace \
  --version $KAGENT_VERSION
```

Verify CRDs:

```bash
kubectl get crd agents.kagent.dev modelconfigs.kagent.dev remotemcpservers.kagent.dev
```

You should see them listed.

### 2. Configure an OpenAI API key (or other provider)

> **Important**  
> - The key must be an **API key** from the OpenAI platform, **not** a ChatGPT token.  
> - The project/org that key belongs to must have **API credits / pay-as-you-go** enabled and a **non-zero usage limit**.

Quick sanity check from your shell:

```bash
export OPENAI_API_KEY="sk-...your-key..."

curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

You should get a JSON list of models.  
If you get `insufficient_quota`, you need to set up billing / credits in the OpenAI dashboard.

### 3. Install kagent (Controller + UI)

```bash
helm install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent \
  --namespace kagent \
  --version $KAGENT_VERSION \
  --set providers.default=openAI \
  --set providers.openAI.apiKey=$OPENAI_API_KEY
```

Check:

```bash
kubectl get pods -n kagent
kubectl get remotemcpservers.kagent.dev -n kagent
```

You should see a `kagent-tool-server` `RemoteMCPServer`.  
That’s the built-in K8s tool server used for `k8s_get_resources`, etc.

---

## Build & Deploy the PSS MCP Server

This MCP server is a **FastMCP-based Python service** that exposes a tool like `analyze_manifest_for_pss`.

### 1. Scaffold (if starting from scratch)

From a working directory:

```bash
kmcp init python pss-mcp-server
cd pss-mcp-server
```

You’ll get something like:

```text
pss-mcp-server/
  src/
    main.py
  kmcp.yaml
  pyproject.toml
  Dockerfile
```

### 2. Implement the PSS Analysis Tool

In `src/main.py` (or another module wired into the same FastMCP instance):

```python
from fastmcp import FastMCP, tool
import yaml
from typing import Any, Dict, List, Literal

mcp = FastMCP("pss-mcp-server")

@tool
def analyze_manifest_for_pss(
    manifest_yaml: str,
    profile: Literal["baseline", "restricted"] = "restricted",
) -> Dict[str, Any]:
    """
    Analyze a Kubernetes Pod/controller manifest for Pod Security Standards
    (baseline/restricted) and return a list of issues with suggested patches.
    """
    # 1) Parse YAML
    # 2) Extract podSpec(s) from Pod/Deployment/DaemonSet/StatefulSet/Job/CronJob
    # 3) Run PSS checks (hostNetwork, hostPath, privileged, caps, sysctls, etc.)
    # 4) Return a structured JSON result
    all_issues: List[Dict[str, Any]] = []

    # ... your PSS checking logic goes here ...

    return {
        "profile": profile,
        "issueCount": len(all_issues),
        "issues": all_issues,
    }

if __name__ == "__main__":
    mcp.run()
```

Verify the tool is visible using the MCP Inspector:

```bash
kmcp run --project-dir .
```

Then, in the Inspector UI → **Tools → List Tools**, you should see the `analyze_manifest_for_pss` tool.

### 3. Build and Load the Image (Kind)

For a local Kind cluster:

```bash
cd pss-mcp-server

kmcp build --project-dir . -t pss-mcp-server:latest --kind-load-cluster kind
```

Now the Kind cluster has the image `pss-mcp-server:latest` available.

> **If using a registry instead of Kind:**  
> Build and `docker push` to something like `ghcr.io/<org>/pss-mcp-server:<tag>` and update the image reference below.

### 4. Configure `kmcp.yaml` for kagent

Edit `kmcp.yaml` so the `MCPServer` metadata matches what the Agent will reference:

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: pss-mcp-server      # Agent will refer to this name
  namespace: kagent         # Same namespace as kagent/Agent
spec:
  deployment:
    image: pss-mcp-server:latest
    port: 3000
    cmd: "python"
    args: ["src/main.py"]
  transportType: "stdio"
```

### 5. Deploy the MCP Server

```bash
kmcp deploy --file kmcp.yaml --image pss-mcp-server:latest
```

Verify:

```bash
kubectl get mcpservers.kagent.dev -n kagent
kubectl describe mcpserver pss-mcp-server -n kagent
kubectl get pods -n kagent | grep pss-mcp-server
```

The `MCPServer` conditions should show `Ready: True` once the pod is healthy.

---

## Create the `pss-remediator` Agent

The Agent uses:

- `kagent-tool-server` for Kubernetes API access.  
- `pss-mcp-server` for PSS analysis.

Apply this manifest:

```bash
kubectl apply -f - << 'EOF'
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: pss-remediator
  namespace: kagent
spec:
  description: "Agent that helps remediate Kubernetes Pod Security Standards misconfigurations."
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |-
      You are a Kubernetes security assistant focused on Pod Security Standards (baseline & restricted).

      You DO have tools that can:
      - Query the live Kubernetes cluster (k8s_get_available_api_resources, k8s_get_resources).
      - Analyze manifests for Pod Security Standards via the PSS MCP server.

      Behavior:
      - When the user asks you to "scan", "check", or "audit" a Pod/Deployment/Namespace:
        1. Use the Kubernetes tools to fetch the live manifest from the cluster.
        2. Pass the manifest YAML into the PSS tool for analysis.
        3. Summarize the issues and propose patches.
      - Only say you *cannot* access the cluster if the k8s tools error or are unavailable.

      Output:
      - Start with a short summary.
      - Then list issues in a table with rule id, path, and message.
      - Finally, show a YAML patch or updated manifest with compliant settings.

    tools:
      # 1) Built-in k8s tools from kagent RemoteMCPServer
      - type: McpServer
        mcpServer:
          apiGroup: kagent.dev
          kind: RemoteMCPServer
          name: kagent-tool-server
          toolNames:
            - k8s_get_available_api_resources
            - k8s_get_resources

      # 2) Custom PSS MCP server
      - type: McpServer
        mcpServer:
          apiGroup: kagent.dev
          kind: MCPServer
          name: pss-mcp-server
          toolNames:
            - analyze_manifest_for_pss
EOF
```

Check the Agent status:

```bash
kubectl get agent -n kagent pss-remediator -o yaml | sed -n '90,150p'
```

You want to see conditions like:

- `type: Accepted` → `status: "True"`  
- `type: Ready` → `status: "True"`  

If `Accepted=False` with messages like `MCPServer ... not found`, make sure the `MCPServer` name and namespace match your `kmcp.yaml` deployment.

---

## Demo: Scan a Deliberately Bad Deployment

### 1. Deploy a PSS-Violating Workload

This `Deployment` is valid Kubernetes, but breaks several PSS rules:

```bash
kubectl apply -f - << 'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pss-demo-bad
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: pss-demo-bad
  template:
    metadata:
      labels:
        app: pss-demo-bad
    spec:
      hostNetwork: true          # PSS violation
      containers:
        - name: bad-nginx
          image: nginx:1.27
          securityContext:
            privileged: true     # PSS violation
            allowPrivilegeEscalation: true
            runAsUser: 0
            capabilities:
              add: ["NET_ADMIN", "SYS_TIME"]
          ports:
            - containerPort: 80
              hostPort: 80       # Allowed by K8s, frowned on by PSS
          volumeMounts:
            - name: logs
              mountPath: /var/log
      volumes:
        - name: logs
          hostPath:              # PSS violation
            path: /var/log
            type: Directory
EOF
```

### 2. Launch kagent Dashboard

```bash
kagent dashboard
```

This port-forwards the UI and opens it in your browser.

In the UI:

1. Go to **Agents**.  
2. Click on **pss-remediator** (namespace `kagent`).  
3. In the chat, run:

```text
Scan the Deployment "pss-demo-bad" in the "default" namespace against the restricted Pod Security Standards profile. List all violations and give me a patch.
```

The Agent should:

1. Use `k8s_get_resources` (via `kagent-tool-server`) to fetch `Deployment/pss-demo-bad`.  
2. Call `analyze_manifest_for_pss` on `pss-mcp-server` with the manifest YAML.  
3. Respond with:
   - A summary of PSS issues.  
   - A list/table of violations (rule id / path / message).  
   - A YAML patch or full fixed manifest.

You can then:

- Apply the patch to fix the `Deployment`.  
- Ask the Agent to re-scan and verify there are no remaining restricted PSS violations.

---

## Troubleshooting

Common issues:

- **OpenAI `insufficient_quota` errors**  
  Ensure your OpenAI project has billing enabled and non-zero quota/limits. Re-run the `curl https://api.openai.com/v1/models` sanity check.

- **`MCPServer ... not found` in Agent status**  
  Verify that:
  - The `MCPServer` name in `kmcp.yaml` matches the name in the Agent spec (`pss-mcp-server`).  
  - Both are in the `kagent` namespace.  
  - The `MCPServer` is `Ready: True`.

- **Agent `Ready` is `False`**  
  Check for:
  - Pod crash loops for `pss-mcp-server` in the `kagent` namespace.  
  - Image name and tag mismatches.  
  - Python import errors inside the MCP server container.

