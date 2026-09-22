# Agent Task Escrow

A decentralized, autonomous task escrow and deliverable adjudication primitive built on GenLayer.

The Agent Task Escrow primitive enables trustless delegation in the agentic economy. A client deposits escrow funds when commissioning knowledge work from an agent (human, AI, or autonomous system). The task specification is defined in natural language alongside explicit, weighted acceptance criteria. When the agent completes the task and submits deliverable URLs, GenLayer validator committees independently fetch the deliverables, evaluate them against each individual criterion in the rubric, and score each requirement on substantive performance rather than superficial formatting.

The contract collapses each adjudication into a discrete payout bucket (fail, a
fixed ladder of partial rungs, or full) and executes automated settlement directly
on-chain as a pure function of that bucket, eliminating the need for centralized
platform arbitrators or subjective manual disputes.

## Live Deployment

- Network: GenLayer Studio Network (studionet)
- Chain ID: 61999
- RPC Endpoint: https://studio.genlayer.com/api
- Contract Address: `0xFb6392D10227955456cd87EDc1fCAEF2C1441513`
- Deployment Transaction: `0xef2d86878d600e8b18c0a00da0bfc51046fe074fcb65933711f85d4d5caa7d81`
- Deployer Address: `0xBC1399c55538eC034d4Da550C03c34Ae0C357f53`
- Explorer URL: https://explorer-studio.genlayer.com/address/0xFb6392D10227955456cd87EDc1fCAEF2C1441513
- GenVM Runner: `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6`

## The Problem

Delegating knowledge work inside the decentralized agentic economy faces a fundamental coordination barrier:

1. Subjectivity of Completion: Unlike quantitative microtasks, knowledge deliverables (codebases, research reports, security audits, dataset synthesis) cannot be validated by simple cryptographic hashes or regex patterns.
2. The Wrapper Trap: A single LLM call acting as an oracle is unreliable and fragile. Prompt gaming, non-deterministic phrasing, and hallucinated scores make raw LLM decisions dangerous for financial settlement.
3. All-or-Nothing Escrow: Traditional smart contracts force binary outcomes (100% payout or 100% refund). Real-world deliverables are often 70% or 85% complete, leading to disputes where either the agent or the client is unfairly penalized.
4. Scope Creep and Superficial Compliance: Agents may submit boilerplate templates or bloated deliverables that match file structure without fulfilling the substantive technical requirements of the brief.

## The GenLayer Solution

The Agent Task Escrow primitive uses GenLayer's Optimistic Democracy consensus to provide autonomous, objective settlement over subjective work:

- Natural Language Task Specification: Clients write briefs and define explicit rubrics with assigned basis-point weights summing to 10000 (100.00%).
- Multi-Node Web Retrieval: Validators independently fetch external deliverables (GitHub pull requests, documentation endpoints, hosted reports) without relying on a centralized oracle.
- Fine-Grained Rubric Scoring: Validators evaluate each acceptance criterion individually, grading each from 0 to 100 points based on substance rather than format.
- Discrete Payout Buckets: The weighted score is collapsed into a single discrete payout bucket (fail, a fixed ladder of partial rungs, or full). Partial work still earns partial pay, but the amount paid depends only on the agreed bucket, never on any one node's exact score.
- Payout-Equivalent Consensus: Validators must independently arrive at the same payout bucket as the leader. Because settlement is a pure function of that bucket, every accepted validator result resolves to the exact same payout amount. There is no score-tolerance band, and a full-payout bucket can never be accepted against a partial-payout bucket.

## Architecture and Consensus Flow

```
+----------------+                                   +------------------+
|     Client     |                                   |      Agent       |
+-------+--------+                                   +--------+---------+
        |                                                     |
        | 1. create_task(escrow, criteria, thresholds)        |
        v                                                     |
+-----------------------------------------------------------+ |
|                     AgentTaskEscrow                       | |
|                  (Intelligent Contract)                   | |
+-----------------------------------------------------------+ |
        ^                                                     |
        | 2. submit_deliverable(urls, notes)                  |
        +-----------------------------------------------------+
        |
        v
+-----------------------------------------------------------------------+
|                     GenLayer Validator Committee                      |
|                                                                       |
|  [Leader Node]                         [Validator Nodes]              |
|  - Fetches deliverable URLs            - Independently fetch URLs     |
|  - Fetches spec document (if set)      - Independently fetch spec     |
|  - Evaluates each criterion (0-100)    - Evaluates each criterion     |
|  - Derives weighted score (0-10000)    - Derives weighted score       |
|  - Quantizes score into payout bucket  - Quantizes into payout bucket |
|  - Proposes verdict JSON               - Enforces equivalence rule:   |
|                                          * Payout buckets must be      |
|                                            EXACTLY equal, else reject  |
|                                                                       |
|                         Equivalence Principle                         |
|                    (Optimistic Democracy Consensus)                   |
+-----------------------------------------------------------------------+
        |
        v
+-----------------------------------------------------------+
|                    Deterministic State                    |
|  - Saves SubmissionRecord with rubric breakdown           |
|  - Transitions Task to ADJUDICATED                        |
|  - Settles payout as a pure function of the agreed bucket |
+-----------------------------------------------------------+
        |
        | 3. settle_payout(task_id)
        v
+-----------------------------------------------------------+
|                    Autonomous Transfer                    |
|  - Emits transfer(agent_payout) to Agent                  |
|  - Emits transfer(client_refund) to Client                |
|  - Transitions Task to SETTLED                            |
+-----------------------------------------------------------+
```

## Rubric Design and Payout Model

### Acceptance Criteria Format
When creating a task, the client supplies a JSON array of structured criteria objects:

```json
[
  {
    "id": "c1_correctness",
    "description": "Functional implementation matches all requested endpoints and schemas",
    "weight_bps": 5000
  },
  {
    "id": "c2_quality",
    "description": "Code passes test suite and includes clean error handling and documentation",
    "weight_bps": 3000
  },
  {
    "id": "c3_architecture",
    "description": "Clean modular architecture without scope creep or extraneous dependencies",
    "weight_bps": 2000
  }
]
```

Criteria requirements enforced on-chain:
- Array length between 1 and 10 criteria.
- Unique criterion IDs.
- Descriptions at least 5 characters long.
- Individual weights must be positive integers.
- Total weights across all criteria must sum exactly to 10000 basis points (100.00%).

### Scoring Formula
The validator committee evaluates each criterion $i$ with a score $S_i \in [0, 100]$:

$$\text{weighted\_score\_bps} = \sum_{i} \frac{S_i \times W_i}{100}$$

Where $W_i$ is the basis-point weight of criterion $i$. This raw score is retained
for auditing, but it is never used directly for money: it is immediately quantized
into a discrete payout bucket (see Financial Settlement Rules), and consensus and
settlement operate exclusively on that bucket.

### Financial Settlement Rules

Settlement never uses a node's raw continuous score. The weighted score is first
quantized into a single discrete payout bucket, and the payout is a pure function
of that bucket. Let $E$ be the locked escrow in wei, $T_{\text{min}}$ be
`min_threshold_bps`, $T_{\text{full}}$ be `full_threshold_bps`, and
$\text{STEP} = 1000$ bps be the partial-bucket width:

1. Complete Failure ($\text{weighted\_score} < T_{\text{min}}$):
   - Payout Bucket = 0
   - Agent Payout = 0, Client Refund = $E$ (100% refund)
   - Evaluation Tier = `CLEAR_FAIL`

2. Full Completion ($\text{weighted\_score} \ge T_{\text{full}}$):
   - Payout Bucket = 10000
   - Agent Payout = $E$ (100% payout), Client Refund = 0
   - Evaluation Tier = `CLEAR_PASS`

3. Partial Credit ($T_{\text{min}} \le \text{weighted\_score} < T_{\text{full}}$):
   - Payout Bucket = $T_{\text{min}} + \lfloor \frac{\text{weighted\_score} - T_{\text{min}}}{\text{STEP}} \rfloor \times \text{STEP}$ (snap down to the lower edge of the STEP-wide bucket; always strictly inside $(0, 10000)$)
   - Agent Payout = $\lfloor \frac{E \times \text{Payout Bucket}}{10000} \rfloor$, Client Refund = $E - \text{Agent Payout}$
   - Evaluation Tier = `PARTIAL_COMPLIANCE`

Because settlement depends only on the discrete bucket, every score inside a bucket
resolves to the identical payout. For example, with $T_{\text{min}} = 5000$ and
$T_{\text{full}} = 8500$, the buckets are 0, 5000, 6000, 7000, 8000, and 10000 —
any score from 7000 to 7999 settles at the 7000 bucket, and a score of 8499
(partial) can never settle like 8500 (full).

## Consensus and Equivalence Principle

Consensus execution runs via `gl.vm.run_nondet_unsafe`:

1. Leader Execution:
   - Fetches deliverable URLs and task specification URL.
   - Formulates a structured adjudication prompt detailing the brief, criteria, and submission notes.
   - Queries LLM with JSON schema enforcement.
   - Computes weighted score deterministically from criterion scores.
   - Quantizes the weighted score into a single discrete payout bucket and derives
     pass/fail and tier from that bucket.

2. Validator Verification:
   - Validates that leader result is a valid return dictionary.
   - Independently fetches the same deliverable and specification URLs.
   - Runs independent LLM prompt, computes an independent score, and quantizes it
     into its own payout bucket via the identical deterministic quantizer.
   - Single Equivalence Rule: `leader_payout_bps == validator_payout_bps`. The
     discrete settlement bucket must match exactly, or the leader is rejected.
     There is no score-tolerance band, and because settlement is a pure function of
     the payout bucket, exact agreement here guarantees that every accepted
     validator result resolves to the same payout amount. A full-payout bucket can
     never validate against a partial-payout bucket.

This design directly enforces the safety property that consensus can only accept
adjudications that pay out identically. Borderline deliverables whose scores
straddle a bucket boundary simply fail to reach consensus (and can be retried or
appealed) rather than settling different amounts — the payout is never ambiguous.

## Smart Contract Interface

### Write Methods

#### `create_task` (payable)
```python
def create_task(
    agent_address: str,
    task_title: str,
    task_description: str,
    task_spec_url: str,
    criteria_json: str,
    deadline: int,
    min_threshold_bps: int,
    full_threshold_bps: int,
) -> str
```
Creates a new escrow task and locks native GEN tokens.
Returns the unique `task_id`.

#### `submit_deliverable`
```python
def submit_deliverable(
    task_id: str,
    deliverable_urls_json: str,
    deliverable_notes: str,
) -> str
```
Called by the designated agent. Triggers validator web fetching and AI rubric consensus adjudication.
Returns a JSON summary of the verdict and payout breakdown.

#### `settle_payout`
```python
def settle_payout(task_id: str) -> str
```
Executes financial transfers (`emit_transfer`) to the agent and client according to the finalized verdict. Can be called by anyone once the task is in `ADJUDICATED` state.

#### `claim_deadline_refund`
```python
def claim_deadline_refund(task_id: str) -> str
```
Allows the client to claim a 100% refund of escrowed funds if the deadline expires without an agent submission.

#### `cancel_task`
```python
def cancel_task(task_id: str) -> str
```
Allows the client to cancel an unstarted task before submission and recover the full escrow balance.

### View Methods

#### `get_task(task_id: str) -> str`
Returns full task state JSON including parties, escrow amount, criteria, deadline, and status.

#### `get_submission(task_id: str) -> str`
Returns deliverable submission details, per-criterion rubric scores, evaluation tier, and reasoning.

#### `get_task_count() -> int`
Returns total number of tasks created.

#### `get_total_escrow_locked() -> str`
Returns total active escrow currently held in the contract in wei.

#### `get_client_tasks(client_address: str) -> str`
Returns a JSON array of task IDs created by the given client address.

#### `get_agent_tasks(agent_address: str) -> str`
Returns a JSON array of task IDs assigned to the given agent address.

#### `preview_payout(task_id: str, score_bps: int) -> str`
Simulates the settlement breakdown for a hypothetical basis-point score using the
exact same discrete payout-bucket quantizer as live settlement, so the preview
shows the real amount a given score would resolve to (not a proportional estimate).

## Integration Guide for Builders

### Python Integration (`genlayer_py`)

```python
import json
import time
from genlayer_py import create_client, studionet, create_account

CLIENT_PRIVATE_KEY = "0x..."
ESCROW_CONTRACT = "0xFb6392D10227955456cd87EDc1fCAEF2C1441513"

client = create_client(chain=studionet, account=create_account(CLIENT_PRIVATE_KEY))

# 1. Define rubric
criteria = [
    {
        "id": "c1_api",
        "description": "REST API with OpenAPI documentation and working auth endpoints",
        "weight_bps": 6000,
    },
    {
        "id": "c2_tests",
        "description": "Automated integration tests with over 80 percent code coverage",
        "weight_bps": 4000,
    },
]

# 2. Create task with 0.1 GEN escrow
tx_hash = client.write_contract(
    address=ESCROW_CONTRACT,
    function_name="create_task",
    args=[
        "0xAgentAddress...",
        "Build Authentication Microservice",
        "Implement a production grade authentication service in Python with JWT support.",
        "https://example.com/specs/auth-spec.pdf",
        json.dumps(criteria),
        int(time.time()) + 86400 * 7,  # 7 days
        5000,  # 50.00% minimum threshold
        9000,  # 90.00% full threshold
    ],
    value=10**17,  # 0.1 GEN
)
receipt = client.wait_for_transaction_receipt(tx_hash)

# 3. Agent submits deliverable
# (From agent client)
submit_tx = agent_client.write_contract(
    address=ESCROW_CONTRACT,
    function_name="submit_deliverable",
    args=[
        "task_0",
        json.dumps(["https://github.com/agent/auth-microservice"]),
        "Finished service implementation with JWT auth and test suite.",
    ],
)
agent_client.wait_for_transaction_receipt(submit_tx)

# 4. Settle payment
settle_tx = client.write_contract(
    address=ESCROW_CONTRACT,
    function_name="settle_payout",
    args=["task_0"],
)
client.wait_for_transaction_receipt(settle_tx)
```

### TypeScript / JavaScript Integration (`genlayer-js`)

```typescript
import { createClient, studionet } from "genlayer-js";

const client = createClient({ chain: studionet });
const ESCROW_CONTRACT = "0xFb6392D10227955456cd87EDc1fCAEF2C1441513";

// Read task status and submission verdict
async function checkTaskStatus(taskId: string) {
  const taskJson = await client.readContract({
    address: ESCROW_CONTRACT,
    functionName: "get_task",
    args: [taskId],
  });
  const task = JSON.parse(taskJson);
  console.log("Task Status:", task.status);

  if (task.status === "ADJUDICATED" || task.status === "SETTLED") {
    const subJson = await client.readContract({
      address: ESCROW_CONTRACT,
      functionName: "get_submission",
      args: [taskId],
    });
    const sub = JSON.parse(subJson);
    console.log("Verdict Passed:", sub.passed);
    console.log("Score (bps):", sub.weighted_score_bps);
    console.log("Agent Payout (wei):", sub.agent_payout_wei);
    console.log("Client Refund (wei):", sub.client_refund_wei);
  }
}
```

## Running Tests

The test suite runs against the live GenLayer Studio Network.

```bash
# Install dependencies
pip install pytest genlayer_py

# Run full test suite
pytest tests/test_agent_task_escrow.py -v
```
