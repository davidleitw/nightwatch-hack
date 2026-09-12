"""Structured output for Guard Room investigations, independent of repair reports."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary_zh: str = Field(min_length=1)
    node_ids: list[str]
    evidence_ids: list[str] = Field(min_length=1)


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cause_zh: str = Field(min_length=1)
    node_ids: list[str]
    supporting_evidence_ids: list[str]
    counterevidence_ids: list[str]
    uncertainty_zh: str = Field(min_length=1)


class InvestigationReportBody(BaseModel):
    """Submit observed facts, hypotheses and limitations; never claim a repair."""
    model_config = ConfigDict(extra="forbid")
    summary_zh: str = Field(min_length=1)
    conclusion: Literal["supported", "inconclusive"]
    findings: list[Finding]
    hypotheses: list[Hypothesis]
    limitations: list[str]
    next_steps: list[str]


REPORT_VERSION = "nightwatch.investigation-report.v1"
SUBMIT_DESCRIPTION = (
    "Finish this investigation by submitting its structured report in Traditional Chinese. "
    "Cite only evidence IDs returned by this session. Use supported only when observations "
    "support your conclusion; otherwise use inconclusive and explain limitations and next steps. "
    "Empty findings are allowed when data queries failed. The backend automatically preserves "
    "session times, prompts, model messages, tool events, evidence and usage; do not copy them "
    "into this tool. Submission does not repair a service or establish verified recovery."
)


def submit_definition() -> dict:
    return {"name": "submit_report", "description": SUBMIT_DESCRIPTION,
            "parameters": InvestigationReportBody.model_json_schema()}


def validate_submission(report: InvestigationReportBody, evidence: list[dict], node_ids: set[str]) -> None:
    available = {item["id"] for item in evidence}
    refs: set[str] = set()
    nodes: set[str] = set()
    for finding in report.findings:
        refs.update(finding.evidence_ids)
        nodes.update(finding.node_ids)
    for hypothesis in report.hypotheses:
        refs.update(hypothesis.supporting_evidence_ids)
        refs.update(hypothesis.counterevidence_ids)
        nodes.update(hypothesis.node_ids)
    if refs - available:
        raise ValueError("Report references evidence not returned in this investigation")
    if nodes - node_ids:
        raise ValueError("Report references an undeclared node")
    if report.conclusion == "supported" and not report.findings:
        raise ValueError("A supported conclusion requires at least one finding with evidence")
    if report.conclusion == "inconclusive" and not report.limitations:
        raise ValueError("An inconclusive report must explain its limitations")
