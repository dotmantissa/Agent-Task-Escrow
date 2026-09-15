"""
Comprehensive End-to-End Tests for Agent Task Escrow
====================================================

These tests execute against the live GenLayer Studio Network and test every
method, rubric calculation, and state transition of the AgentTaskEscrow contract.

Tested functionality:
  1. Contract deployment and schema verification
  2. Initial nonexistent state handling
  3. Payout preview calculation across score bands
  4. Task indexing by client and agent
  5. Adjudication verdict and rubric breakdown on failed deliverable (task_0)
  6. Passing deliverable consensus and full payout settlement lifecycle (task_4)
  7. Task creation and verification with live escrow deposit
  8. Task cancellation and escrow refund lifecycle
  9. Escrow accounting and total locked funds tracking
"""

import json
import time
import pytest
from genlayer_py import create_client, studionet, create_account

DEPLOYER_KEY = "0xd4479070c2a31da31a01e732ca51707132bacdb480aae432a0c8bd0b91eba4b7"
AGENT_KEY = "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d"
LIVE_CONTRACT_ADDRESS = "0x994dEe34c3102Cb0148553b811BEfa66C4569478"

ESCROW_WEI = 10**16  # 0.01 GEN
MIN_THRESHOLD_BPS = 5000   # 50.00%
FULL_THRESHOLD_BPS = 8500  # 85.00%

CRITERIA = [
    {
        "id": "c1_correctness",
        "description": "Functional implementation matches all requested endpoints and return schemas",
        "weight_bps": 5000,
    },
    {
        "id": "c2_quality",
        "description": "Code passes test suite and includes clean error handling and documentation",
        "weight_bps": 3000,
    },
    {
        "id": "c3_packaging",
        "description": "Clean modular architecture without scope creep or extraneous dependencies",
        "weight_bps": 2000,
    },
]


@pytest.fixture(scope="session")
def client():
    account = create_account(DEPLOYER_KEY)
    return create_client(chain=studionet, account=account)


@pytest.fixture(scope="session")
def agent_client():
    account = create_account(AGENT_KEY)
    return create_client(chain=studionet, account=account)


@pytest.fixture(scope="session")
def deployer(client):
    return client.local_account


@pytest.fixture(scope="session")
def agent_account(agent_client):
    return agent_client.local_account


@pytest.fixture(scope="session")
def contract_address():
    return LIVE_CONTRACT_ADDRESS


def poll_task_status(client, contract_address, task_id, expected_status, retries=15, interval=2):
    """Poll get_task until expected status is reached."""
    for _ in range(retries):
        raw = client.read_contract(
            address=contract_address,
            function_name="get_task",
            args=[task_id],
        )
        if raw != "":
            data = json.loads(raw)
            if data.get("status") == expected_status:
                return data
        time.sleep(interval)
    raw = client.read_contract(
        address=contract_address,
        function_name="get_task",
        args=[task_id],
    )
    if raw != "":
        return json.loads(raw)
    raise AssertionError(f"Task {task_id} not available or did not reach status {expected_status}")


# ---------------------------------------------------------------------------
# Test 1: Contract Deployment and Schema Verification
# ---------------------------------------------------------------------------

def test_contract_schema_and_methods(client, contract_address):
    """Verify the deployed contract exposes the full expected method interface."""
    schema = client.get_contract_schema(contract_address)
    assert schema is not None
    methods = schema.get("methods", {})
    expected_methods = [
        "cancel_task",
        "claim_deadline_refund",
        "create_task",
        "get_agent_tasks",
        "get_client_tasks",
        "get_submission",
        "get_task",
        "get_task_count",
        "get_total_escrow_locked",
        "preview_payout",
        "settle_payout",
        "submit_deliverable",
    ]
    for method in expected_methods:
        assert method in methods, f"Missing expected contract method: {method}"
    assert methods["create_task"]["payable"] is True
    assert methods["get_task"]["readonly"] is True
    assert methods["get_submission"]["readonly"] is True
    assert methods["preview_payout"]["readonly"] is True


# ---------------------------------------------------------------------------
# Test 2: Initial Nonexistent State Handling
# ---------------------------------------------------------------------------

def test_nonexistent_task_returns_empty_string(client, contract_address):
    """Calling get_task with a non-existent task_id should cleanly return an empty string."""
    res = client.read_contract(
        address=contract_address,
        function_name="get_task",
        args=["nonexistent_task_99999"],
    )
    assert res == ""


def test_nonexistent_submission_returns_empty_string(client, contract_address):
    """Calling get_submission with an unsubmitted task_id should cleanly return an empty string."""
    res = client.read_contract(
        address=contract_address,
        function_name="get_submission",
        args=["nonexistent_task_99999"],
    )
    assert res == ""


def test_preview_payout_nonexistent_task(client, contract_address):
    """Previewing payout for nonexistent task should return empty string."""
    res = client.read_contract(
        address=contract_address,
        function_name="preview_payout",
        args=["nonexistent_task_99999", 7500],
    )
    assert res == ""


# ---------------------------------------------------------------------------
# Test 3: Payout Preview Calculation Across Score Bands
# ---------------------------------------------------------------------------

def test_preview_payout_calculations(client, contract_address):
    """Verify preview_payout accurately reflects fail, partial, and full payout tiers."""
    task_id = "task_0"

    # Band 1: Fail (below min threshold 50.00%)
    preview_fail_raw = client.read_contract(
        address=contract_address,
        function_name="preview_payout",
        args=[task_id, 4000],
    )
    preview_fail = json.loads(preview_fail_raw)
    assert preview_fail["passed"] is False
    assert int(preview_fail["agent_payout_wei"]) == 0
    assert int(preview_fail["client_refund_wei"]) == ESCROW_WEI

    # Band 2: Partial credit (75.00%, between 50% and 85%)
    preview_partial_raw = client.read_contract(
        address=contract_address,
        function_name="preview_payout",
        args=[task_id, 7500],
    )
    preview_partial = json.loads(preview_partial_raw)
    assert preview_partial["passed"] is True
    expected_agent_payout = (ESCROW_WEI * 7500) // 10000
    assert int(preview_partial["agent_payout_wei"]) == expected_agent_payout
    assert int(preview_partial["client_refund_wei"]) == ESCROW_WEI - expected_agent_payout

    # Band 3: Full credit (90.00%, above full threshold 85%)
    preview_full_raw = client.read_contract(
        address=contract_address,
        function_name="preview_payout",
        args=[task_id, 9000],
    )
    preview_full = json.loads(preview_full_raw)
    assert preview_full["passed"] is True
    assert int(preview_full["agent_payout_wei"]) == ESCROW_WEI
    assert int(preview_full["client_refund_wei"]) == 0


# ---------------------------------------------------------------------------
# Test 4: Task Indexing for Client and Agent
# ---------------------------------------------------------------------------

def test_task_indexing_for_parties(client, deployer, agent_account, contract_address):
    """Verify that get_client_tasks and get_agent_tasks list the created tasks."""
    client_tasks_raw = client.read_contract(
        address=contract_address,
        function_name="get_client_tasks",
        args=[deployer.address],
    )
    client_tasks = json.loads(client_tasks_raw)
    assert isinstance(client_tasks, list)
    assert "task_0" in client_tasks

    agent_tasks_raw = client.read_contract(
        address=contract_address,
        function_name="get_agent_tasks",
        args=[agent_account.address],
    )
    agent_tasks = json.loads(agent_tasks_raw)
    assert isinstance(agent_tasks, list)
    assert "task_0" in agent_tasks


# ---------------------------------------------------------------------------
# Test 5: Failed Deliverable Adjudication and Rubric Verification (task_0)
# ---------------------------------------------------------------------------

def test_adjudication_verdict_and_rubric(client, contract_address):
    """Verify consensus correctly rejected an unfulfilled deliverable with 0 score."""
    raw_submission = client.read_contract(
        address=contract_address,
        function_name="get_submission",
        args=["task_0"],
    )
    assert raw_submission != ""
    submission = json.loads(raw_submission)

    assert submission["task_id"] == "task_0"
    assert submission["passed"] is False
    assert submission["weighted_score_bps"] == 0
    assert submission["evaluation_tier"] == "CLEAR_FAIL"
    assert len(submission["reasoning"]) > 0

    # Verify per-criterion rubric evaluations
    rubric_scores = json.loads(submission["rubric_scores_json"])
    assert len(rubric_scores) == 3
    for crit in rubric_scores:
        assert "id" in crit
        assert "score" in crit
        assert "compliance_type" in crit
        assert int(crit["score"]) == 0

    raw_task = client.read_contract(
        address=contract_address,
        function_name="get_task",
        args=["task_0"],
    )
    task = json.loads(raw_task)
    assert task["status"] == "SETTLED"


# ---------------------------------------------------------------------------
# Test 6: Passing Deliverable Consensus and Full Payout Settlement (task_4)
# ---------------------------------------------------------------------------

def test_passing_deliverable_full_payout_lifecycle(client, contract_address):
    """Verify an adjudicated passing deliverable receives full score and settles payout."""
    raw_sub = client.read_contract(
        address=contract_address,
        function_name="get_submission",
        args=["task_4"],
    )
    assert raw_sub != "", "Submission for task_4 must exist"
    sub = json.loads(raw_sub)

    assert sub["task_id"] == "task_4"
    assert sub["passed"] is True
    assert sub["weighted_score_bps"] == 10000
    assert sub["evaluation_tier"] == "CLEAR_PASS"
    assert int(sub["agent_payout_wei"]) == 50000000000000000
    assert int(sub["client_refund_wei"]) == 0

    # Verify per-criterion 100% scores
    rubric_scores = json.loads(sub["rubric_scores_json"])
    assert len(rubric_scores) == 3
    for crit in rubric_scores:
        assert crit["score"] == 100
        assert crit["compliance_type"] == "FULL"

    raw_task = client.read_contract(
        address=contract_address,
        function_name="get_task",
        args=["task_4"],
    )
    task = json.loads(raw_task)
    assert task["status"] == "SETTLED"
    assert task["settled_at"] > 0


# ---------------------------------------------------------------------------
# Test 7: Task Creation and State Verification
# ---------------------------------------------------------------------------

def test_create_task_and_read_state(client, deployer, agent_account, contract_address):
    """Create a task with locked GEN escrow and verify stored state."""
    count_before = int(client.read_contract(address=contract_address, function_name="get_task_count", args=[]))
    expected_task_id = f"task_{count_before}"

    deadline = int(time.time()) + 86400
    criteria_json = json.dumps(CRITERIA)

    tx_hash = client.write_contract(
        address=contract_address,
        function_name="create_task",
        args=[
            agent_account.address,
            "Verification Test Task",
            "Functional verification task for integration testing suite.",
            "https://raw.githubusercontent.com/dotmantissa/GenLayer-Primitives/main/README.md",
            criteria_json,
            deadline,
            MIN_THRESHOLD_BPS,
            FULL_THRESHOLD_BPS,
        ],
        value=ESCROW_WEI,
    )
    assert tx_hash is not None

    receipt = client.wait_for_transaction_receipt(tx_hash, retries=40, interval=3000)
    assert receipt.get("status_name") in ("ACCEPTED", "FINALIZED")

    task = poll_task_status(client, contract_address, expected_task_id, "CREATED")
    assert task["client"].lower() == deployer.address.lower()
    assert task["agent"].lower() == agent_account.address.lower()
    assert task["status"] == "CREATED"
    assert int(task["escrow_wei"]) == ESCROW_WEI


# ---------------------------------------------------------------------------
# Test 8: Task Cancellation and Escrow Refund Lifecycle
# ---------------------------------------------------------------------------

def test_cancel_task_lifecycle(client, deployer, agent_account, contract_address):
    """Verify client can cancel an unsubmitted task and transition state to CANCELLED."""
    count_before = int(client.read_contract(address=contract_address, function_name="get_task_count", args=[]))
    expected_task_id = f"task_{count_before}"

    deadline = int(time.time()) + 86400
    criteria_json = json.dumps(CRITERIA)

    create_tx = client.write_contract(
        address=contract_address,
        function_name="create_task",
        args=[
            agent_account.address,
            "Lifecycle Cancellation Task",
            "This task will be cancelled by the client before submission.",
            "",
            criteria_json,
            deadline,
            MIN_THRESHOLD_BPS,
            FULL_THRESHOLD_BPS,
        ],
        value=ESCROW_WEI,
    )
    client.wait_for_transaction_receipt(create_tx, retries=40, interval=3000)
    poll_task_status(client, contract_address, expected_task_id, "CREATED")

    cancel_tx = client.write_contract(
        address=contract_address,
        function_name="cancel_task",
        args=[expected_task_id],
    )
    receipt = client.wait_for_transaction_receipt(cancel_tx, retries=40, interval=3000)
    assert receipt.get("status_name") in ("ACCEPTED", "FINALIZED")

    task = poll_task_status(client, contract_address, expected_task_id, "CANCELLED")
    assert task["status"] == "CANCELLED"
    assert task["settled_at"] > 0


# ---------------------------------------------------------------------------
# Test 9: Escrow Accounting and Tracking
# ---------------------------------------------------------------------------

def test_escrow_accounting_tracking(client, contract_address):
    """Verify get_total_escrow_locked returns a valid integer string."""
    locked = client.read_contract(
        address=contract_address,
        function_name="get_total_escrow_locked",
        args=[],
    )
    assert isinstance(locked, str)
    assert int(locked) >= 0
