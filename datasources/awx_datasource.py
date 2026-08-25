from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from dify_plugin.entities.datasource import (
    DatasourceGetPagesResponse,
    DatasourceMessage,
    GetOnlineDocumentPageContentRequest,
    OnlineDocumentInfo,
    OnlineDocumentPage,
)
from dify_plugin.interfaces.datasource.online_document import OnlineDocumentDatasource

_TIMEOUT = 30
_PAGE_SIZE = 200
_RECENT_PAGE_SIZE = 50
_RECENT_RUNS_PER_TEMPLATE = 10


class AwxDatasource(OnlineDocumentDatasource):

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _creds(self) -> tuple[str, str, bool]:
        c = self.runtime.credentials
        return (
            (c.get("awx_url") or "").rstrip("/"),
            c.get("awx_token") or "",
            bool(c.get("ssl_verify", False)),
        )

    def _get(self, url: str, token: str, ssl_verify: bool, params: dict | None = None) -> dict:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            params=params or {},
            verify=ssl_verify,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def _list_all(self, base_url: str, token: str, ssl_verify: bool, endpoint: str, extra_params: dict | None = None) -> list[dict]:
        """Paginate through AWX results and return all items."""
        params = {"page_size": _PAGE_SIZE, "order_by": "name", **(extra_params or {})}
        results: list[dict] = []
        url = f"{base_url}{endpoint}"
        while url:
            data = self._get(url, token, ssl_verify, params)
            results.extend(data.get("results", []))
            url = data.get("next") or ""
            params = {}  # next URL already includes params
        return results

    @staticmethod
    def _fmt_dt(iso: str | None) -> str:
        if not iso:
            return "—"
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            return iso or "—"

    @staticmethod
    def _duration(elapsed: float | None) -> str:
        if elapsed is None:
            return "—"
        m, s = divmod(int(elapsed), 60)
        return f"{m}m {s}s" if m else f"{s}s"

    @staticmethod
    def _transition_ids(values: list[Any] | None) -> list[str]:
        result = []
        for value in values or []:
            if isinstance(value, dict):
                node_id = value.get("id")
            else:
                node_id = value
            if node_id is not None:
                result.append(str(node_id))
        return result

    @staticmethod
    def _status_emoji(status: str) -> str:
        return {
            "successful": "✅",
            "failed": "❌",
            "error": "🔴",
            "canceled": "⛔",
            "running": "🔄",
            "pending": "⏳",
            "waiting": "⏳",
        }.get(status, "❓")

    # ------------------------------------------------------------------ #
    #  _get_pages                                                          #
    # ------------------------------------------------------------------ #

    def _get_pages(self, datasource_parameters: dict[str, Any]) -> DatasourceGetPagesResponse:
        awx_url, token, ssl_verify = self._creds()

        hostname = urlparse(awx_url).hostname or awx_url

        # Fetch job templates and workflow templates in parallel via sequential calls
        job_templates = self._list_all(awx_url, token, ssl_verify, "/api/v2/job_templates/")
        wf_templates = self._list_all(awx_url, token, ssl_verify, "/api/v2/workflow_job_templates/")

        pages: list[OnlineDocumentPage] = []
        _now = datetime.now(timezone.utc).isoformat()

        # One page per Job Template
        for jt in job_templates:
            pages.append(
                OnlineDocumentPage(
                    page_id=f"jt_{jt['id']}",
                    page_name=f"[JT] {jt['name']}",
                    type="job_template",
                    last_edited_time=jt.get("modified") or _now,
                    parent_id=None,
                )
            )

        # One page per Workflow Template
        for wft in wf_templates:
            pages.append(
                OnlineDocumentPage(
                    page_id=f"wjt_{wft['id']}",
                    page_name=f"[WF] {wft['name']}",
                    type="workflow_template",
                    last_edited_time=wft.get("modified") or _now,
                    parent_id=None,
                )
            )

        # Special aggregate pages
        pages.append(
            OnlineDocumentPage(
                page_id="recent_jobs",
                page_name=f"Recent Jobs (last {_RECENT_PAGE_SIZE})",
                type="recent_jobs",
                last_edited_time=_now,
                parent_id=None,
            )
        )
        pages.append(
            OnlineDocumentPage(
                page_id="recent_wf_jobs",
                page_name=f"Recent Workflow Jobs (last {_RECENT_PAGE_SIZE})",
                type="recent_wf_jobs",
                last_edited_time=_now,
                parent_id=None,
            )
        )

        info = OnlineDocumentInfo(
            workspace_name=f"AWX @ {hostname}",
            workspace_icon="",
            workspace_id=awx_url,
            pages=pages,
            total=len(pages),
        )
        return DatasourceGetPagesResponse(result=[info])

    # ------------------------------------------------------------------ #
    #  _get_content                                                        #
    # ------------------------------------------------------------------ #

    def _get_content(
        self, page: GetOnlineDocumentPageContentRequest
    ) -> Generator[DatasourceMessage, None, None]:
        awx_url, token, ssl_verify = self._creds()
        pid = page.page_id or ""

        if pid.startswith("jt_"):
            content = self._content_job_template(awx_url, token, ssl_verify, pid[3:])
        elif pid.startswith("wjt_"):
            content = self._content_workflow_template(awx_url, token, ssl_verify, pid[4:])
        elif pid == "recent_jobs":
            content = self._content_recent_jobs(awx_url, token, ssl_verify)
        elif pid == "recent_wf_jobs":
            content = self._content_recent_wf_jobs(awx_url, token, ssl_verify)
        else:
            content = f"# Unknown resource\n\nPage ID `{pid}` is not recognized."

        yield self.create_variable_message("content", content)

    # ------------------------------------------------------------------ #
    #  Content formatters                                                  #
    # ------------------------------------------------------------------ #

    def _content_job_template(self, base: str, token: str, ssl: bool, jt_id: str) -> str:
        jt = self._get(f"{base}/api/v2/job_templates/{jt_id}/", token, ssl)

        # Recent runs
        runs_data = self._get(
            f"{base}/api/v2/job_templates/{jt_id}/jobs/",
            token, ssl,
            {"order_by": "-id", "page_size": _RECENT_RUNS_PER_TEMPLATE},
        )
        runs = runs_data.get("results", [])

        lines = [
            f"# Job Template: {jt.get('name', '—')}",
            "",
            f"**ID**: {jt.get('id')} | **Projeto**: {jt.get('summary_fields', {}).get('project', {}).get('name', '—')} | **Playbook**: `{jt.get('playbook', '—')}`",
            f"**Inventário**: {jt.get('summary_fields', {}).get('inventory', {}).get('name', '—')} | **Credencial**: {jt.get('summary_fields', {}).get('credentials', [{}])[0].get('name', '—') if jt.get('summary_fields', {}).get('credentials') else '—'}",
            f"**Criado**: {self._fmt_dt(jt.get('created'))} | **Atualizado**: {self._fmt_dt(jt.get('modified'))}",
            f"**Limit**: `{jt.get('limit') or '(todos)'}` | **Tags de Job**: `{jt.get('job_tags') or '—'}` | **Forks**: {jt.get('forks', 0)}",
            f"**Verbosidade**: {jt.get('verbosity', 0)} | **Timeout**: {jt.get('timeout', 0)}s",
        ]

        desc = (jt.get("description") or "").strip()
        if desc:
            lines += ["", f"**Descrição**: {desc}"]

        extra_vars = (jt.get("extra_vars") or "").strip()
        if extra_vars:
            lines += ["", "**Variáveis Extra:**", "```yaml", extra_vars, "```"]

        lines += ["", f"## Últimas {len(runs)} Execuções", ""]
        if runs:
            lines.append("| ID | Status | Iniciado | Duração | Disparado por |")
            lines.append("|----|--------|---------|---------|---------------|")
            for r in runs:
                st = r.get("status", "—")
                launched_by = r.get("summary_fields", {}).get("launched_by", {}).get("username", "—")
                lines.append(
                    f"| {r.get('id')} | {self._status_emoji(st)} {st} "
                    f"| {self._fmt_dt(r.get('started'))} "
                    f"| {self._duration(r.get('elapsed'))} "
                    f"| {launched_by} |"
                )
        else:
            lines.append("_Nenhuma execução encontrada._")

        return "\n".join(lines)

    def _content_workflow_template(self, base: str, token: str, ssl: bool, wft_id: str) -> str:
        wft = self._get(f"{base}/api/v2/workflow_job_templates/{wft_id}/", token, ssl)

        # Workflow nodes
        nodes_data = self._get(f"{base}/api/v2/workflow_job_templates/{wft_id}/workflow_nodes/", token, ssl)
        nodes = nodes_data.get("results", [])

        # Recent workflow runs
        runs_data = self._get(
            f"{base}/api/v2/workflow_job_templates/{wft_id}/workflow_jobs/",
            token, ssl,
            {"order_by": "-id", "page_size": _RECENT_RUNS_PER_TEMPLATE},
        )
        runs = runs_data.get("results", [])

        org_name = wft.get("summary_fields", {}).get("organization", {}).get("name", "—")

        lines = [
            f"# Workflow Template: {wft.get('name', '—')}",
            "",
            f"**ID**: {wft.get('id')} | **Organização**: {org_name}",
            f"**Criado**: {self._fmt_dt(wft.get('created'))} | **Atualizado**: {self._fmt_dt(wft.get('modified'))}",
        ]

        desc = (wft.get("description") or "").strip()
        if desc:
            lines += ["", f"**Descrição**: {desc}"]

        # Nodes summary
        lines += ["", f"## Nós do Workflow ({len(nodes)} nós)", ""]
        if nodes:
            for node in nodes:
                jt_name = node.get("summary_fields", {}).get("unified_job_template", {}).get("name", f"node_{node.get('id')}")
                node_type = node.get("summary_fields", {}).get("unified_job_template", {}).get("unified_job_type", "")
                success_nodes = self._transition_ids(node.get("success_nodes"))
                failure_nodes = self._transition_ids(node.get("failure_nodes"))
                always_nodes = self._transition_ids(node.get("always_nodes"))
                transitions = []
                if success_nodes:
                    transitions.append(f"✅→ nós {', '.join(success_nodes)}")
                if failure_nodes:
                    transitions.append(f"❌→ nós {', '.join(failure_nodes)}")
                if always_nodes:
                    transitions.append(f"🔄→ nós {', '.join(always_nodes)}")
                transition_str = " | ".join(transitions) if transitions else "terminal"
                lines.append(f"- **[{node.get('id')}]** `{jt_name}` ({node_type}) — {transition_str}")
        else:
            lines.append("_Sem nós configurados._")

        # Recent runs
        lines += ["", f"## Últimas {len(runs)} Execuções", ""]
        if runs:
            lines.append("| ID | Status | Iniciado | Duração | Disparado por |")
            lines.append("|----|--------|---------|---------|---------------|")
            for r in runs:
                st = r.get("status", "—")
                launched_by = r.get("summary_fields", {}).get("launched_by", {}).get("username", "—")
                lines.append(
                    f"| {r.get('id')} | {self._status_emoji(st)} {st} "
                    f"| {self._fmt_dt(r.get('started'))} "
                    f"| {self._duration(r.get('elapsed'))} "
                    f"| {launched_by} |"
                )
        else:
            lines.append("_Nenhuma execução encontrada._")

        return "\n".join(lines)

    def _content_recent_jobs(self, base: str, token: str, ssl: bool) -> str:
        data = self._get(
            f"{base}/api/v2/jobs/",
            token, ssl,
            {"order_by": "-id", "page_size": _RECENT_PAGE_SIZE},
        )
        jobs = data.get("results", [])

        lines = [
            f"# Recent Jobs (últimos {len(jobs)})",
            "",
            "| ID | Template | Status | Iniciado | Duração | Disparado por |",
            "|----|----------|--------|---------|---------|---------------|",
        ]
        for j in jobs:
            st = j.get("status", "—")
            jt_name = j.get("summary_fields", {}).get("job_template", {}).get("name", "—")
            launched_by = j.get("summary_fields", {}).get("launched_by", {}).get("username", "—")
            lines.append(
                f"| {j.get('id')} | {jt_name} | {self._status_emoji(st)} {st} "
                f"| {self._fmt_dt(j.get('started'))} "
                f"| {self._duration(j.get('elapsed'))} "
                f"| {launched_by} |"
            )

        # Status summary
        from collections import Counter
        counts = Counter(j.get("status", "unknown") for j in jobs)
        lines += ["", "## Resumo por Status", ""]
        for status, count in sorted(counts.items(), key=lambda x: -x[1]):
            lines.append(f"- {self._status_emoji(status)} **{status}**: {count}")

        return "\n".join(lines)

    def _content_recent_wf_jobs(self, base: str, token: str, ssl: bool) -> str:
        data = self._get(
            f"{base}/api/v2/workflow_jobs/",
            token, ssl,
            {"order_by": "-id", "page_size": _RECENT_PAGE_SIZE},
        )
        jobs = data.get("results", [])

        lines = [
            f"# Recent Workflow Jobs (últimos {len(jobs)})",
            "",
            "| ID | Workflow | Status | Iniciado | Duração | Disparado por |",
            "|----|----------|--------|---------|---------|---------------|",
        ]
        for j in jobs:
            st = j.get("status", "—")
            wft_name = j.get("summary_fields", {}).get("workflow_job_template", {}).get("name", "—")
            launched_by = j.get("summary_fields", {}).get("launched_by", {}).get("username", "—")
            lines.append(
                f"| {j.get('id')} | {wft_name} | {self._status_emoji(st)} {st} "
                f"| {self._fmt_dt(j.get('started'))} "
                f"| {self._duration(j.get('elapsed'))} "
                f"| {launched_by} |"
            )

        from collections import Counter
        counts = Counter(j.get("status", "unknown") for j in jobs)
        lines += ["", "## Resumo por Status", ""]
        for status, count in sorted(counts.items(), key=lambda x: -x[1]):
            lines.append(f"- {self._status_emoji(status)} **{status}**: {count}")

        return "\n".join(lines)
