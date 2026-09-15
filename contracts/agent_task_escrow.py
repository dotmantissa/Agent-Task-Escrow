# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

"""
Agent Task Escrow
=================

Purpose
-------
The agentic economy runs on delegation: a client hires an agent (human, AI, or
autonomous bot) to complete a knowledge work task, and wants to pay on completion.
However, completion for knowledge work is inherently subjective. Did the agent
actually complete what was requested, or merely produce output that resembles it?
Did the deliverable satisfy the quality bar specified in the brief?

This contract holds client funds in escrow and adjudicates whether an agent's
submitted deliverable satisfies the task specification. The task specification is
defined in natural language at creation time with explicit, weighted acceptance
criteria. When the agent submits deliverable URLs, GenLayer validators independently
fetch the deliverables, evaluate them against each individual criterion in the
rubric, and score each criterion on its substance rather than superficial format.

Why GenLayer?
-------------
Subjective knowledge work verification cannot be resolved by deterministic code.
A regex or word counter cannot judge whether a research paper contains genuine
analysis, whether code meets functional requirements, or whether scope creep was
substituted for requested features.

GenLayer enables decentralized consensus over subjective evaluations through
Optimistic Democracy:
1. Decentralized Web Fetching: Validators independently retrieve deliverables from
   public URLs (GitHub, IPFS, web endpoints, document stores).
2. Rubric-Based Scoring: Validators do not give a simplistic binary answer. They
   score each acceptance criterion individually and calculate a weighted overall
   result in basis points (0 to 10000).
3. Partial Payout Support: When work is partially complete, the contract calculates
   a proportional payout for the agent and refunds the remainder to the client.
4. Equivalence Principle: Validators must agree both on the baseline pass/fail
   outcome and on the partial credit score within a defined tolerance band.

Consensus Model
---------------
Adjudication executes via gl.vm.run_nondet_unsafe with a custom leader/validator
equivalence rule:
- Leader:
  1. Fetches deliverable URLs and the task specification URL (if provided).
  2. Parses acceptance criteria and prompts the LLM to score each criterion
     from 0 to 100 based on substance compliance, format compliance, and scope.
  3. Derives the weighted overall score and pass/fail determination.
  4. Returns structured JSON containing individual rubric scores and reasoning.
- Validator:
  1. Independently fetches the same deliverable and specification URLs.
  2. Independently prompts the LLM and calculates criterion scores.
  3. Enforces equivalence:
     - Both nodes must agree on the pass/fail determination.
     - Both nodes must agree on the weighted score within a tolerance band of
       1000 basis points (10.00%).
     - Reject if evaluation tiers conflict (e.g. CLEAR_PASS vs CLEAR_FAIL).

Contract Lifecycle
------------------
1. Task Creation:
   Client calls create_task, providing task details, acceptance criteria with
   weights summing to 10000 basis points, deadline, and thresholds.
   Client attaches GEN tokens as escrow.
2. Deliverable Submission:
   Agent calls submit_deliverable with deliverable URLs and delivery notes.
   Validators run independent adjudication and commit the rubric verdict.
3. Settlement:
   Anyone calls settle_payout to transfer the earned share to the agent and
   refund any remaining balance to the client.
4. Cancellation or Expiry:
   If the agent does not submit before the deadline, client claims a full refund
   via claim_deadline_refund. Client can also cancel unstarted tasks via cancel_task.
"""

import json
import re
import typing
from dataclasses import dataclass
from datetime import datetime, timezone

from genlayer import *


# ---------------------------------------------------------------------------
# Storage dataclasses
# ---------------------------------------------------------------------------

@allow_storage
@dataclass
class TaskRecord:
    """
    Represents an escrowed task agreement between a client and an agent.
    """
    task_id: str
    client: Address
    agent: Address
    escrow_wei: u256
    task_title: str
    task_description: str
    task_spec_url: str
    criteria_json: str
    deadline: u64
    min_threshold_bps: u32
    full_threshold_bps: u32
    status: str
    created_at: u64
    adjudicated_at: u64
    settled_at: u64


@allow_storage
@dataclass
class SubmissionRecord:
    """
    Represents an agent deliverable submission and its consensus verdict.
    """
    task_id: str
    deliverable_urls_json: str
    deliverable_notes: str
    submitted_at: u64
    passed: bool
    weighted_score_bps: u32
    agent_payout_wei: u256
    client_refund_wei: u256
    rubric_scores_json: str
    evaluation_tier: str
    reasoning: str


# ---------------------------------------------------------------------------
# Helper functions for defensive JSON parsing
# ---------------------------------------------------------------------------

def _clean_json(raw: typing.Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        txt = raw.strip()
        first = txt.find("{")
        last = txt.rfind("}")
        if first >= 0 and last > first:
            txt = txt[first : last + 1]
        txt = re.sub(r",(?!\s*?[\{\[\"\'\w])", "", txt)
        loaded = json.loads(txt)
        if isinstance(loaded, dict):
            return loaded
    raise gl.vm.UserError("[LLM_ERROR] LLM response was not a valid JSON object")


def _coerce_bool(raw: typing.Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    txt = str(raw).strip().lower()
    if txt in ("true", "yes", "1", "pass", "passed", "accepted"):
        return True
    if txt in ("false", "no", "0", "fail", "failed", "rejected"):
        return False
    return False


# ---------------------------------------------------------------------------
# Main Contract
# ---------------------------------------------------------------------------

class AgentTaskEscrow(gl.Contract):
    """
    Agent Task Escrow: Decentralized subjective deliverable adjudication
    and rubric-based settlement primitive for GenLayer.
    """

    # Storage slots
    tasks: TreeMap[str, TaskRecord]
    submissions: TreeMap[str, SubmissionRecord]
    total_tasks: u256
    total_escrow_locked_wei: u256
    owner: Address

    # Indexes for querying tasks by client and agent
    client_task_count: TreeMap[Address, u32]
    client_tasks: TreeMap[str, str]
    agent_task_count: TreeMap[Address, u32]
    agent_tasks: TreeMap[str, str]

    def __init__(self) -> None:
        self.owner = gl.message.sender_address
        self.total_tasks = u256(0)
        self.total_escrow_locked_wei = u256(0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require(self, condition: bool, message: str) -> None:
        if not condition:
            raise gl.vm.UserError(message)

    def _now(self) -> u64:
        try:
            if hasattr(gl, "message_raw") and isinstance(gl.message_raw, dict) and "datetime" in gl.message_raw:
                txt = str(gl.message_raw["datetime"]).strip()
                if txt:
                    if txt.endswith("Z"):
                        txt = txt[:-1] + "+00:00"
                    dt = datetime.fromisoformat(txt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return u64(int(dt.timestamp()))
            return u64(int(datetime.now(timezone.utc).timestamp()))
        except Exception:
            return u64(0)

    def _parse_criteria(self, criteria_json: str) -> list:
        try:
            parsed = json.loads(criteria_json)
        except Exception:
            raise gl.vm.UserError("criteria_json must be valid JSON")

        self._require(isinstance(parsed, list), "criteria_json must be a JSON array")
        self._require(1 <= len(parsed) <= 10, "Task must define between 1 and 10 criteria")

        total_weight = 0
        seen_ids = set()
        sanitized = []

        for item in parsed:
            self._require(isinstance(item, dict), "Each criterion must be a JSON object")
            cid = str(item.get("id", "")).strip()
            desc = str(item.get("description", "")).strip()
            self._require(len(cid) >= 1, "Criterion id cannot be empty")
            self._require(cid not in seen_ids, f"Duplicate criterion id: {cid}")
            seen_ids.add(cid)
            self._require(len(desc) >= 5, f"Criterion description too short for id: {cid}")

            try:
                weight = int(item.get("weight_bps", 0))
            except Exception:
                raise gl.vm.UserError(f"Invalid weight_bps for criterion: {cid}")

            self._require(weight > 0, f"Criterion weight must be positive for id: {cid}")
            total_weight += weight
            sanitized.append({
                "id": cid,
                "description": desc,
                "weight_bps": weight,
            })

        self._require(
            total_weight == 10000,
            f"Criterion weights must sum exactly to 10000 basis points (got {total_weight})"
        )
        return sanitized

    def _compute_payout(self, escrow_wei: int, score_bps: int, min_threshold_bps: int, full_threshold_bps: int) -> tuple:
        """
        Calculates agent payout and client refund in wei based on score.
        - If score < min_threshold_bps: 0 payout, full refund.
        - If score >= full_threshold_bps: full payout, 0 refund.
        - Otherwise: proportional payout based on score_bps.
        """
        if score_bps < min_threshold_bps:
            return 0, escrow_wei
        if score_bps >= full_threshold_bps:
            return escrow_wei, 0

        agent_payout = (escrow_wei * score_bps) // 10000
        client_refund = escrow_wei - agent_payout
        return agent_payout, client_refund

    # ------------------------------------------------------------------
    # Public write methods
    # ------------------------------------------------------------------

    @gl.public.write.payable
    def create_task(
        self,
        agent_address: str,
        task_title: str,
        task_description: str,
        task_spec_url: str,
        criteria_json: str,
        deadline: int,
        min_threshold_bps: int,
        full_threshold_bps: int,
    ) -> str:
        """
        Create a new escrowed task agreement and lock the client payment.

        Parameters
        ----------
        agent_address : str
            Address of the hired agent authorized to submit deliverables.
        task_title : str
            Brief human-readable title of the task.
        task_description : str
            Detailed natural-language description of the requested work.
        task_spec_url : str
            Optional URL to an external detailed specification, RFP, or brief.
        criteria_json : str
            JSON array of criteria objects with id, description, and weight_bps.
            Weights must sum exactly to 10000 (100.00%).
        deadline : int
            Unix timestamp by which the deliverable must be submitted.
        min_threshold_bps : int
            Minimum score in basis points required for any payout (e.g. 5000 = 50.00%).
        full_threshold_bps : int
            Score in basis points required for 100% payout (e.g. 9000 = 90.00%).

        Returns
        -------
        str
            The unique task_id.
        """
        client = gl.message.sender_address
        escrow = gl.message.value

        self._require(escrow > u256(0), "Escrow deposit must be non-zero; send GEN with call")
        agent = Address(agent_address)
        self._require(agent != client, "Agent address cannot be the same as client address")
        self._require(3 <= len(task_title) <= 200, "Task title must be between 3 and 200 characters")
        self._require(len(task_description) >= 10, "Task description must be at least 10 characters")

        now_ts = self._now()
        if now_ts > 0:
            self._require(deadline > int(now_ts), "Deadline must be in the future")

        self._require(100 <= min_threshold_bps <= 10000, "min_threshold_bps must be between 100 and 10000")
        self._require(
            min_threshold_bps <= full_threshold_bps <= 10000,
            "full_threshold_bps must be between min_threshold_bps and 10000"
        )

        # Validate rubric criteria structure and weights
        self._parse_criteria(criteria_json)

        task_id = f"task_{int(self.total_tasks)}"
        self.total_tasks = u256(int(self.total_tasks) + 1)

        task = TaskRecord(
            task_id=task_id,
            client=client,
            agent=agent,
            escrow_wei=escrow,
            task_title=task_title,
            task_description=task_description,
            task_spec_url=task_spec_url,
            criteria_json=criteria_json,
            deadline=u64(deadline),
            min_threshold_bps=u32(min_threshold_bps),
            full_threshold_bps=u32(full_threshold_bps),
            status="CREATED",
            created_at=now_ts,
            adjudicated_at=u64(0),
            settled_at=u64(0),
        )

        self.tasks[task_id] = task
        self.total_escrow_locked_wei = u256(int(self.total_escrow_locked_wei) + int(escrow))

        # Index task for client
        c_count = int(self.client_task_count.get(client, u32(0)))
        self.client_tasks[f"{str(client).lower()}:{c_count}"] = task_id
        self.client_task_count[client] = u32(c_count + 1)

        # Index task for agent
        a_count = int(self.agent_task_count.get(agent, u32(0)))
        self.agent_tasks[f"{str(agent).lower()}:{a_count}"] = task_id
        self.agent_task_count[agent] = u32(a_count + 1)

        return task_id

    @gl.public.write
    def submit_deliverable(
        self,
        task_id: str,
        deliverable_urls_json: str,
        deliverable_notes: str,
    ) -> str:
        """
        Submit a completed deliverable for independent validator adjudication.

        Parameters
        ----------
        task_id : str
            The identifier of the task.
        deliverable_urls_json : str
            JSON array of up to 5 publicly accessible deliverable URLs.
        deliverable_notes : str
            Notes and context provided by the agent explaining the submission.

        Returns
        -------
        str
            JSON summary of the adjudication verdict, rubric scores, and payout.
        """
        sender = gl.message.sender_address

        self._require(task_id in self.tasks, "Task ID not found")
        task = self.tasks[task_id]

        self._require(sender == task.agent, "Only the designated agent may submit deliverables")
        self._require(task.status == "CREATED", f"Task is not in CREATED state (current: {task.status})")

        now_ts = self._now()
        if now_ts > 0 and int(task.deadline) > 0:
            self._require(int(now_ts) <= int(task.deadline), "Submission deadline has passed")

        self._require(len(deliverable_notes) >= 10, "Deliverable notes must be at least 10 characters")

        try:
            urls_list = json.loads(deliverable_urls_json)
        except Exception:
            raise gl.vm.UserError("deliverable_urls_json must be a valid JSON array of URLs")

        self._require(isinstance(urls_list, list), "deliverable_urls_json must be a JSON array")
        self._require(1 <= len(urls_list) <= 5, "Provide between 1 and 5 deliverable URLs")
        for u in urls_list:
            self._require(isinstance(u, str) and len(u) >= 8, "Each deliverable URL must be a valid HTTP string")

        criteria_list = self._parse_criteria(task.criteria_json)

        # Local snapshots for closure capture
        task_title_mem = str(task.task_title)
        task_desc_mem = str(task.task_description)
        spec_url_mem = str(task.task_spec_url)
        min_thresh_mem = int(task.min_threshold_bps)
        full_thresh_mem = int(task.full_threshold_bps)
        escrow_mem = int(task.escrow_wei)

        # -------------------------------------------------------------------
        # Non-deterministic Consensus Adjudication
        # -------------------------------------------------------------------

        def leader_fn() -> dict:
            # 1. Fetch deliverable contents
            deliverables_data = []
            for d_url in urls_list:
                try:
                    resp = gl.nondet.web.get(str(d_url))
                    body_raw = getattr(resp, "body", b"")
                    if isinstance(body_raw, bytes):
                        text = body_raw.decode("utf-8", errors="replace")[:4000]
                    else:
                        text = str(body_raw)[:4000]
                    status_code = getattr(resp, "status", 200)
                    deliverables_data.append({"url": d_url, "content": text, "status": status_code})
                except Exception as ex:
                    deliverables_data.append({"url": d_url, "content": "", "error": str(ex), "status": 0})

            # 2. Fetch specification document if URL provided
            spec_content = ""
            if len(spec_url_mem) >= 8:
                try:
                    s_resp = gl.nondet.web.get(spec_url_mem)
                    s_body = getattr(s_resp, "body", b"")
                    if isinstance(s_body, bytes):
                        spec_content = s_body.decode("utf-8", errors="replace")[:4000]
                    else:
                        spec_content = str(s_body)[:4000]
                except Exception as s_ex:
                    spec_content = f"(Unable to fetch spec: {s_ex})"

            # 3. Format criteria text
            criteria_text = ""
            for c in criteria_list:
                criteria_text += f"- [{c['id']}] (Weight: {c['weight_bps'] / 100:.2f}%): {c['description']}\n"

            prompt = f"""You are a professional, objective adjudicator for an autonomous agent task escrow contract.
Evaluate whether the submitted deliverable satisfies the task specification according to the explicit acceptance rubric.

TASK SPECIFICATION:
Title: {task_title_mem}
Description: {task_desc_mem}
External Spec Content: {spec_content}

ACCEPTANCE CRITERIA RUBRIC:
{criteria_text}

MINIMUM PASS THRESHOLD: {min_thresh_mem / 100:.2f}%
FULL PAYOUT THRESHOLD: {full_thresh_mem / 100:.2f}%

AGENT'S SUBMISSION NOTES:
{deliverable_notes}

FETCHED DELIVERABLE DATA:
{json.dumps(deliverables_data, ensure_ascii=False)[:8000]}

EVALUATION RULES:
1. Examine each acceptance criterion individually. Assign a score from 0 to 100 points:
   - 100: Criterion completely fulfilled with high quality and verifiable substance.
   - 50 to 99: Criterion partially fulfilled or minor defects present.
   - 1 to 49: Serious deficiencies or only superficial resemblance.
   - 0: Completely missing or unfulfilled.
2. Format compliance vs. Substance compliance:
   - Penalize submissions that merely copy boilerplate, templates, or produce shallow output without substantive work.
3. Scope creep:
   - Check if unrequested additions were substituted in place of core required features.
4. Compliance type per criterion:
   - Choose one of: "FULL", "PARTIAL_SUBSTANCE", "FORMAT_ONLY", "NON_COMPLIANT".
5. Evaluation tier:
   - "CLEAR_PASS" if overall weighted score is >= full threshold.
   - "PARTIAL_COMPLIANCE" if overall score is between min threshold and full threshold.
   - "CLEAR_FAIL" if overall score is below min threshold.

Respond with a single valid JSON object formatted exactly as:
{{
  "criteria_evaluations": [
    {{
      "id": "criterion_id",
      "score": integer (0 to 100),
      "compliance_type": "FULL" or "PARTIAL_SUBSTANCE" or "FORMAT_ONLY" or "NON_COMPLIANT",
      "notes": "concise explanation for this criterion score"
    }}
  ],
  "format_compliance_score": integer (0 to 100),
  "substance_compliance_score": integer (0 to 100),
  "scope_creep_detected": true or false,
  "evaluation_tier": "CLEAR_PASS" or "PARTIAL_COMPLIANCE" or "CLEAR_FAIL",
  "reasoning": "summary evaluation of the deliverable (max 250 words)"
}}
"""
            raw_output = gl.nondet.exec_prompt(prompt, response_format="json")
            parsed = _clean_json(raw_output)

            # Defensive parsing and deterministic score computation
            raw_evals = parsed.get("criteria_evaluations", [])
            evals_by_id = {}
            if isinstance(raw_evals, list):
                for item in raw_evals:
                    if isinstance(item, dict):
                        cid = str(item.get("id", "")).strip()
                        evals_by_id[cid] = item

            # Deterministically calculate the weighted score from parsed criteria scores
            computed_score_bps = 0
            sanitized_evals = []

            for c in criteria_list:
                cid = c["id"]
                weight = c["weight_bps"]
                score_item = evals_by_id.get(cid, {})
                try:
                    item_score = int(score_item.get("score", 0))
                except Exception:
                    item_score = 0
                item_score = max(0, min(100, item_score))

                c_type = str(score_item.get("compliance_type", "PARTIAL_SUBSTANCE")).strip().upper()
                if c_type not in ("FULL", "PARTIAL_SUBSTANCE", "FORMAT_ONLY", "NON_COMPLIANT"):
                    c_type = "PARTIAL_SUBSTANCE"

                notes = str(score_item.get("notes", ""))[:200]

                sanitized_evals.append({
                    "id": cid,
                    "score": item_score,
                    "weight_bps": weight,
                    "compliance_type": c_type,
                    "notes": notes,
                })

                computed_score_bps += (item_score * weight) // 100

            computed_score_bps = max(0, min(10000, computed_score_bps))
            passed = bool(computed_score_bps >= min_thresh_mem)

            tier = str(parsed.get("evaluation_tier", "")).strip().upper()
            if computed_score_bps >= full_thresh_mem:
                tier = "CLEAR_PASS"
            elif passed:
                tier = "PARTIAL_COMPLIANCE"
            else:
                tier = "CLEAR_FAIL"

            format_score = max(0, min(100, int(parsed.get("format_compliance_score", 50))))
            substance_score = max(0, min(100, int(parsed.get("substance_compliance_score", 50))))
            scope_creep = _coerce_bool(parsed.get("scope_creep_detected", False))
            reasoning = str(parsed.get("reasoning", ""))[:400]

            return {
                "passed": passed,
                "weighted_score_bps": computed_score_bps,
                "evaluation_tier": tier,
                "criteria_evaluations": sanitized_evals,
                "format_compliance_score": format_score,
                "substance_compliance_score": substance_score,
                "scope_creep_detected": scope_creep,
                "reasoning": reasoning,
            }

        def validator_fn(leaders_res: typing.Any) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False

            leaders_raw = getattr(leaders_res, "calldata", None)
            if not isinstance(leaders_raw, dict):
                try:
                    leader_dict = _clean_json(leaders_raw)
                except Exception:
                    return False
            else:
                leader_dict = leaders_raw

            try:
                mine = leader_fn()
            except Exception:
                return False

            leader_passed = _coerce_bool(leader_dict.get("passed", False))
            my_passed = bool(mine["passed"])

            # Rule 1: Both leader and validator MUST agree on the binary pass/fail verdict
            if leader_passed != my_passed:
                return False

            try:
                leader_score = int(leader_dict.get("weighted_score_bps", 0))
            except Exception:
                leader_score = 0
            my_score = int(mine["weighted_score_bps"])

            # Rule 2: Scores must be within tolerance band of 1000 bps (10.00%)
            if abs(leader_score - my_score) > 1000:
                return False

            # Rule 3: Evaluation tiers must not conflict sharply
            leader_tier = str(leader_dict.get("evaluation_tier", "")).strip().upper()
            my_tier = str(mine["evaluation_tier"]).strip().upper()
            if (leader_tier == "CLEAR_PASS" and my_tier == "CLEAR_FAIL") or \
               (leader_tier == "CLEAR_FAIL" and my_tier == "CLEAR_PASS"):
                return False

            return True

        # Execute multi-validator consensus
        jury_result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        if hasattr(jury_result, "get") and not isinstance(jury_result, dict):
            try:
                verdict = jury_result.get()
            except TypeError:
                verdict = jury_result
        else:
            verdict = jury_result

        if not isinstance(verdict, dict):
            verdict = _clean_json(verdict)

        # -------------------------------------------------------------------
        # Deterministic settlement calculation and state update
        # -------------------------------------------------------------------

        passed = _coerce_bool(verdict.get("passed", False))
        try:
            score_bps = int(verdict.get("weighted_score_bps", 0))
        except Exception:
            score_bps = 0
        score_bps = max(0, min(10000, score_bps))

        agent_payout, client_refund = self._compute_payout(
            escrow_mem,
            score_bps,
            min_thresh_mem,
            full_thresh_mem,
        )

        tier = str(verdict.get("evaluation_tier", "PARTIAL_COMPLIANCE"))
        reasoning = str(verdict.get("reasoning", ""))[:400]
        rubric_scores = json.dumps(verdict.get("criteria_evaluations", []))

        submission = SubmissionRecord(
            task_id=task_id,
            deliverable_urls_json=deliverable_urls_json,
            deliverable_notes=deliverable_notes[:500],
            submitted_at=now_ts,
            passed=passed,
            weighted_score_bps=u32(score_bps),
            agent_payout_wei=u256(agent_payout),
            client_refund_wei=u256(client_refund),
            rubric_scores_json=rubric_scores,
            evaluation_tier=tier,
            reasoning=reasoning,
        )

        self.submissions[task_id] = submission
        task.status = "ADJUDICATED"
        task.adjudicated_at = now_ts
        self.tasks[task_id] = task

        return json.dumps({
            "task_id": task_id,
            "status": "ADJUDICATED",
            "passed": passed,
            "weighted_score_bps": score_bps,
            "evaluation_tier": tier,
            "agent_payout_wei": str(agent_payout),
            "client_refund_wei": str(client_refund),
            "reasoning": reasoning,
        }, sort_keys=True)

    @gl.public.write
    def settle_payout(self, task_id: str) -> str:
        """
        Execute the financial payout and refund transfers for an adjudicated task.

        Parameters
        ----------
        task_id : str
            The task ID.

        Returns
        -------
        str
            JSON confirmation with transferred amounts.
        """
        self._require(task_id in self.tasks, "Task not found")
        task = self.tasks[task_id]

        self._require(task.status == "ADJUDICATED", f"Task is not adjudicated (status: {task.status})")
        self._require(task_id in self.submissions, "Submission record missing")
        submission = self.submissions[task_id]

        agent_payout = int(submission.agent_payout_wei)
        client_refund = int(submission.client_refund_wei)
        total_transfer = agent_payout + client_refund

        task.status = "SETTLED"
        task.settled_at = self._now()
        self.tasks[task_id] = task

        # Release funds
        if agent_payout > 0:
            agent_iface = gl.get_contract_at(task.agent)
            agent_iface.emit_transfer(value=u256(agent_payout), on="finalized")

        if client_refund > 0:
            client_iface = gl.get_contract_at(task.client)
            client_iface.emit_transfer(value=u256(client_refund), on="finalized")

        current_locked = int(self.total_escrow_locked_wei)
        self.total_escrow_locked_wei = u256(max(0, current_locked - total_transfer))

        return json.dumps({
            "task_id": task_id,
            "status": "SETTLED",
            "agent": str(task.agent),
            "agent_payout_wei": str(agent_payout),
            "client": str(task.client),
            "client_refund_wei": str(client_refund),
        }, sort_keys=True)

    @gl.public.write
    def claim_deadline_refund(self, task_id: str) -> str:
        """
        Claim a full refund of escrow if the deadline passed without agent submission.

        Parameters
        ----------
        task_id : str
            The task ID.

        Returns
        -------
        str
            JSON confirmation of the refund.
        """
        self._require(task_id in self.tasks, "Task not found")
        task = self.tasks[task_id]

        self._require(task.status == "CREATED", f"Task is not in CREATED state (status: {task.status})")

        now_ts = self._now()
        self._require(int(now_ts) > int(task.deadline), "Task deadline has not passed yet")

        escrow = int(task.escrow_wei)
        task.status = "EXPIRED"
        task.settled_at = now_ts
        self.tasks[task_id] = task

        if escrow > 0:
            client_iface = gl.get_contract_at(task.client)
            client_iface.emit_transfer(value=u256(escrow), on="finalized")

        current_locked = int(self.total_escrow_locked_wei)
        self.total_escrow_locked_wei = u256(max(0, current_locked - escrow))

        return json.dumps({
            "task_id": task_id,
            "status": "EXPIRED",
            "client": str(task.client),
            "refunded_wei": str(escrow),
        }, sort_keys=True)

    @gl.public.write
    def cancel_task(self, task_id: str) -> str:
        """
        Cancel a task prior to submission and return escrow to the client.

        Parameters
        ----------
        task_id : str
            The task ID.

        Returns
        -------
        str
            JSON confirmation of the cancellation.
        """
        sender = gl.message.sender_address

        self._require(task_id in self.tasks, "Task not found")
        task = self.tasks[task_id]

        self._require(sender == task.client, "Only the client may cancel this task")
        self._require(task.status == "CREATED", f"Cannot cancel task in {task.status} status")

        escrow = int(task.escrow_wei)
        now_ts = self._now()
        task.status = "CANCELLED"
        task.settled_at = now_ts
        self.tasks[task_id] = task

        if escrow > 0:
            client_iface = gl.get_contract_at(task.client)
            client_iface.emit_transfer(value=u256(escrow), on="finalized")

        current_locked = int(self.total_escrow_locked_wei)
        self.total_escrow_locked_wei = u256(max(0, current_locked - escrow))

        return json.dumps({
            "task_id": task_id,
            "status": "CANCELLED",
            "client": str(task.client),
            "refunded_wei": str(escrow),
        }, sort_keys=True)

    # ------------------------------------------------------------------
    # View methods
    # ------------------------------------------------------------------

    @gl.public.view
    def get_task(self, task_id: str) -> str:
        """
        Retrieve full details for a task.

        Parameters
        ----------
        task_id : str
            The task ID.

        Returns
        -------
        str
            JSON string containing all task fields, or empty string if not found.
        """
        if task_id not in self.tasks:
            return ""

        t = self.tasks[task_id]
        return json.dumps({
            "task_id": t.task_id,
            "client": str(t.client),
            "agent": str(t.agent),
            "escrow_wei": str(int(t.escrow_wei)),
            "task_title": t.task_title,
            "task_description": t.task_description,
            "task_spec_url": t.task_spec_url,
            "criteria_json": t.criteria_json,
            "deadline": int(t.deadline),
            "min_threshold_bps": int(t.min_threshold_bps),
            "full_threshold_bps": int(t.full_threshold_bps),
            "status": t.status,
            "created_at": int(t.created_at),
            "adjudicated_at": int(t.adjudicated_at),
            "settled_at": int(t.settled_at),
        }, sort_keys=True)

    @gl.public.view
    def get_submission(self, task_id: str) -> str:
        """
        Retrieve deliverable submission and rubric verdict details for a task.

        Parameters
        ----------
        task_id : str
            The task ID.

        Returns
        -------
        str
            JSON string with submission record, or empty string if not submitted.
        """
        if task_id not in self.submissions:
            return ""

        s = self.submissions[task_id]
        return json.dumps({
            "task_id": s.task_id,
            "deliverable_urls_json": s.deliverable_urls_json,
            "deliverable_notes": s.deliverable_notes,
            "submitted_at": int(s.submitted_at),
            "passed": s.passed,
            "weighted_score_bps": int(s.weighted_score_bps),
            "agent_payout_wei": str(int(s.agent_payout_wei)),
            "client_refund_wei": str(int(s.client_refund_wei)),
            "rubric_scores_json": s.rubric_scores_json,
            "evaluation_tier": s.evaluation_tier,
            "reasoning": s.reasoning,
        }, sort_keys=True)

    @gl.public.view
    def get_task_count(self) -> int:
        """
        Return the total number of tasks created.
        """
        return int(self.total_tasks)

    @gl.public.view
    def get_total_escrow_locked(self) -> str:
        """
        Return the total GEN (in wei) currently locked across all active tasks.
        """
        return str(int(self.total_escrow_locked_wei))

    @gl.public.view
    def get_client_tasks(self, client_address: str) -> str:
        """
        Return all task IDs created by a specific client.

        Parameters
        ----------
        client_address : str
            The client's address.

        Returns
        -------
        str
            JSON array of task ID strings.
        """
        client = Address(client_address)
        count = int(self.client_task_count.get(client, u32(0)))
        task_ids = []
        for i in range(count):
            key = f"{str(client).lower()}:{i}"
            if key in self.client_tasks:
                task_ids.append(self.client_tasks[key])
        return json.dumps(task_ids)

    @gl.public.view
    def get_agent_tasks(self, agent_address: str) -> str:
        """
        Return all task IDs assigned to a specific agent.

        Parameters
        ----------
        agent_address : str
            The agent's address.

        Returns
        -------
        str
            JSON array of task ID strings.
        """
        agent = Address(agent_address)
        count = int(self.agent_task_count.get(agent, u32(0)))
        task_ids = []
        for i in range(count):
            key = f"{str(agent).lower()}:{i}"
            if key in self.agent_tasks:
                task_ids.append(self.agent_tasks[key])
        return json.dumps(task_ids)

    @gl.public.view
    def preview_payout(self, task_id: str, score_bps: int) -> str:
        """
        Simulate the financial distribution for a hypothetical score on a task.

        Parameters
        ----------
        task_id : str
            The task ID.
        score_bps : int
            Hypothetical score in basis points (0 to 10000).

        Returns
        -------
        str
            JSON object detailing the potential agent payout and client refund.
        """
        if task_id not in self.tasks:
            return ""

        t = self.tasks[task_id]
        escrow = int(t.escrow_wei)
        min_bps = int(t.min_threshold_bps)
        full_bps = int(t.full_threshold_bps)
        score = max(0, min(10000, score_bps))

        agent_payout, client_refund = self._compute_payout(escrow, score, min_bps, full_bps)

        return json.dumps({
            "task_id": task_id,
            "escrow_wei": str(escrow),
            "simulated_score_bps": score,
            "min_threshold_bps": min_bps,
            "full_threshold_bps": full_bps,
            "passed": score >= min_bps,
            "agent_payout_wei": str(agent_payout),
            "client_refund_wei": str(client_refund),
        }, sort_keys=True)
