from typing import Any, Dict, List, Literal
import yaml
from fastmcp import FastMCP, tool

mcp = FastMCP("pss-helper")


def _check_pod_spec(pod_spec: Dict[str, Any], profile: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []

    # 1) host* flags (Baseline/Restricted) :contentReference[oaicite:3]{index=3}
    for field in ["hostNetwork", "hostPID", "hostIPC"]:
        if pod_spec.get(field, False):
            issues.append(
                {
                    "id": f"PSS-{profile.upper()}-{field}",
                    "level": "error",
                    "path": f"spec.{field}",
                    "message": f"{field}=true is not allowed under {profile} profile.",
                    "recommendedPatch": f"spec:\n  {field}: false\n",
                }
            )

    # 2) hostPath volumes
    for i, vol in enumerate(pod_spec.get("volumes", [])):
        if "hostPath" in vol:
            issues.append(
                {
                    "id": f"PSS-{profile.upper()}-hostPath",
                    "level": "error",
                    "path": f"spec.volumes[{i}].hostPath",
                    "message": "hostPath volumes are disallowed under baseline/restricted.",
                    "recommendedPatch": "# Replace hostPath with PVC / ConfigMap / Secret / emptyDir, etc.\n",
                }
            )

    # 3) container securityContext checks
    for i, c in enumerate(pod_spec.get("containers", [])):
        sc = c.get("securityContext") or {}
        name = c.get("name", f"containers[{i}]")
        base_path = f"spec.containers[{i}].securityContext"

        # Privileged
        if sc.get("privileged", False):
            issues.append(
                {
                    "id": "PSS-PRIVILEGED",
                    "level": "error",
                    "path": f"{base_path}.privileged",
                    "message": f"Container {name} runs privileged. Not allowed in baseline/restricted.",
                    "recommendedPatch": f"{base_path}:\n  privileged: false\n",
                }
            )

        # AllowPrivilegeEscalation
        if sc.get("allowPrivilegeEscalation", True):
            issues.append(
                {
                    "id": "PSS-ALLOW_PRIV_ESC",
                    "level": "error",
                    "path": f"{base_path}.allowPrivilegeEscalation",
                    "message": f"Container {name} allows privilege escalation.",
                    "recommendedPatch": f"{base_path}:\n  allowPrivilegeEscalation: false\n",
                }
            )

        # Restricted: runAsNonRoot
        if profile == "restricted":
            run_as_non_root = sc.get("runAsNonRoot")
            run_as_user = sc.get("runAsUser")
            if run_as_non_root is not True and run_as_user in (None, 0):
                issues.append(
                    {
                        "id": "PSS-RESTRICTED-RUN_AS_NON_ROOT",
                        "level": "error",
                        "path": base_path,
                        "message": f"Container {name} must be configured to run as non-root under restricted profile.",
                        "recommendedPatch": (
                            f"{base_path}:\n"
                            "  runAsNonRoot: true\n"
                            "  runAsUser: 1000\n"
                        ),
                    }
                )

        # Capabilities.add – only NET_BIND_SERVICE is usually allowed in restricted :contentReference[oaicite:4]{index=4}
        caps = (sc.get("capabilities") or {}).get("add") or []
        forbidden_caps = [cap for cap in caps if cap != "NET_BIND_SERVICE"]
        if forbidden_caps and profile == "restricted":
            issues.append(
                {
                    "id": "PSS-RESTRICTED-CAPS",
                    "level": "error",
                    "path": f"{base_path}.capabilities.add",
                    "message": f"Container {name} adds capabilities {forbidden_caps} which violate restricted profile.",
                    "recommendedPatch": (
                        f"{base_path}:\n"
                        "  capabilities:\n"
                        "    drop: [\"ALL\"]\n"
                        "    # Optional: add NET_BIND_SERVICE only if needed\n"
                        "    # add: [\"NET_BIND_SERVICE\"]\n"
                    ),
                }
            )

    return issues


def _extract_pod_spec(doc: Dict[str, Any]) -> Dict[str, Any]:
    kind = (doc.get("kind") or "").lower()
    spec = doc.get("spec") or {}

    if kind == "pod":
        return spec
    if kind in ("deployment", "replicaset", "statefulset", "daemonset"):
        return spec.get("template", {}).get("spec", {}) or {}
    if kind == "job":
        return spec.get("template", {}).get("spec", {}) or {}
    if kind == "cronjob":
        return spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec", {}) or {}

    # Fallback – treat spec as podSpec-ish
    return spec


@tool
def analyze_manifest_for_pss(
    manifest_yaml: str,
    profile: Literal["baseline", "restricted"] = "restricted",
) -> Dict[str, Any]:
    """
    Analyze a Kubernetes Pod / controller manifest for Pod Security Standards (baseline/restricted)
    and return a list of issues with suggested patch snippets.

    Expected usage pattern:
    - The agent first fetches a manifest (e.g. via kagent's k8s_get_resources tool)
    - Then passes the manifest YAML into this tool for analysis
    """
    docs = [d for d in yaml.safe_load_all(manifest_yaml) if d]
    all_issues: List[Dict[str, Any]] = []

    for doc in docs:
        meta = doc.get("metadata") or {}
        name = meta.get("name")
        namespace = meta.get("namespace")
        kind = doc.get("kind")

        pod_spec = _extract_pod_spec(doc)
        issues = _check_pod_spec(pod_spec, profile)

        for issue in issues:
            issue.setdefault("resourceKind", kind)
            issue.setdefault("resourceName", name)
            issue.setdefault("namespace", namespace)

        all_issues.extend(issues)

    return {
        "profile": profile,
        "issueCount": len(all_issues),
        "issues": all_issues,
    }


if __name__ == "__main__":
    mcp.run()
