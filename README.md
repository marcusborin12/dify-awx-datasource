# AWX Datasource Plugin for Dify

Connects Ansible AWX / Ansible Tower to Dify's Knowledge Base pipeline, enabling AI agents to search and retrieve information about automation jobs, templates, and execution history via RAG (Retrieval-Augmented Generation).

**Source repository:** https://github.com/marcusborin12/dify-awx-datasource

## What it indexes

| Page type | Content |
|-----------|---------|
| `[JT] <name>` | Job Template details: playbook, inventory, credentials, variables, last 10 runs |
| `[WF] <name>` | Workflow Template details: nodes, transitions, last 10 runs |
| `Recent Jobs` | Last 50 job executions (all templates) with status, duration, and triggered-by |
| `Recent Workflow Jobs` | Last 50 workflow job executions |

## Setup

### 1. Generate an AWX API Token

In AWX: **User menu → User Settings → Tokens → Add**

- Application: (leave blank for personal token)
- Scope: **Read** (sufficient for indexing)

### 2. Configure the Plugin Credentials

| Field | Value |
|-------|-------|
| AWX Base URL | `https://your-awx-host` (no trailing slash) |
| AWX API Token | Token generated above |
| Verify SSL | Disable for self-signed internal certificates |

### 3. Use in Knowledge Base

1. Dify Console → **Knowledge** → **New Dataset**
2. Select **AWX Datasource** as the data source
3. Choose which templates/pages to index
4. Set indexing mode to **High Quality**
5. Click **Save & Process**

## Requirements

- Dify >= 1.9.0
- AWX >= 3.8 / Ansible Tower >= 3.6 (API v2)

## Packaging

```bash
pip install dify-plugin-cli
dify plugin package . -o awx_datasource.difypkg
```

Then install in Dify: **Plugins → Install from local file**.
