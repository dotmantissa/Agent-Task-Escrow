"""
End-to-End Integration Tests for Agent Task Escrow
==================================================

These tests execute against the live GenLayer Studio Network deployment and cover
the contract's deterministic behavior, lifecycle transitions, and — most
importantly — the payout-equivalence property required for consensus safety:

  1. Contract deployment and schema verification
  2. Initial nonexistent-state handling
  3. Discrete payout-bucket ladder via preview_payout (the settlement-layer proof
     that every score inside a bucket resolves to the SAME payout, and that a
     full-payout bucket never coincides with a partial-payout bucket)
  4. Task indexing by client and agent
  5. Task creation with live escrow deposit
  6. Task cancellation and escrow refund lifecycle
  7. Escrow accounting and total locked funds tracking

The adjudication path (submit_deliverable) runs a nondeterministic multi-node LLM
consensus round and is not asserted here for exact outcomes. Its safety-critical
guarantee — that consensus only accepts payout-equivalent results — is proven
deterministically and exhaustively in tests/test_payout_equivalence.py, and is
demonstrated live at the settlement layer by the preview_payout ladder below,
which uses the identical on-chain quantizer.
"""

import json
import time
import pytest
from genlayer_py import create_client, studionet, create_account

DEPLOYER_KEY = "0xd4479070c2a31da31a01e732ca51707132bacdb480aae432a0c8bd0b91eba4b7"
AGENT_KEY = "0x4f3edf983ac636a65a842ce7c78d9aa706d3b113bce9c46f30d7d21715b23b1d"
LIVE_CONTRACT_ADDRESS = "0xFb6392D10227955456cd87EDc1fCAEF2C1441513"

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


def poll_task_status(client, contract_address, task_id, expected_status, retries=20, interval=2):
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


def _create_task(client, agent_address, title, description):
    """Create a task and return its task_id once readable in CREATED state."""
    count_before = int(client.read_contract(
        address=LIVE_CONTRACT_ADDRESS, function_name="get_task_count", args=[]))
    expected_task_id = f"task_{count_before}"
    deadline = int(time.time()) + 86400
    tx_hash = client.write_contract(
        address=LIVE_CONTRACT_ADDRESS,
        function_name="create_task",
        args=[
            agent_address,
            title,
            description,
            "",
            json.dumps(CRITERIA),
            deadline,
            MIN_THRESHOLD_BPS,
            FULL_THRESHOLD_BPS,
        ],
        value=ESCROW_WEI,
    )
    client.wait_for_transaction_receipt(tx_hash, retries=60, interval=3000)
    poll_task_status(client, LIVE_CONTRACT_ADDRESS, expected_task_id, "CREATED")
    return expected_task_id


@pytest.fixture(scope="session")
def seeded_task(client, agent_account):
    """Create one task used by the read-only ladder and indexing assertions."""
    task_id = _create_task(
        client,
        agent_account.address,
        "Payout Bucket Verification Task",
        "Deterministic task used to verify discrete payout bucketing and indexing.",
    )
    return task_id


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
# Test 3: Discrete Payout-Bucket Ladder (settlement-layer equivalence proof)
# ---------------------------------------------------------------------------

def test_preview_payout_bucket_ladder(client, contract_address, seeded_task):
    """Verify preview_payout resolves scores to discrete payout buckets on-chain.

    This is the live settlement-layer demonstration of the reviewer requirement:
    settlement is a pure function of a discrete payout bucket, so every score
    within one bucket resolves to the exact same payout, and a full-payout bucket
    is never reachable from a partial score. With thresholds 5000 / 8500 and a
    STEP of 1000 bps, the buckets are 0, 5000, 6000, 7000, 8000, and 10000.
    """
    # (score_bps, expected payout_bps bucket)
    ladder = [
        (4000, 0),      # below min -> fail, full refund
        (5000, 5000),
        (5999, 5000),   # same bucket as 5000
        (6000, 6000),
        (7000, 7000),
        (7500, 7000),   # partial: 7500 settles from the 7000 bucket, NOT proportional 7500
        (7999, 7000),   # same bucket as 7000
        (8000, 8000),
        (8499, 8000),   # partial, just below full threshold
        (8500, 10000),  # at full threshold -> full payout
        (9000, 10000),
        (10000, 10000),
    ]

    previews = {}
    for score, expected_bucket in ladder:
        raw = client.read_contract(
            address=contract_address,
            function_name="preview_payout",
            args=[seeded_task, score],
        )
        assert raw != "", f"preview_payout returned empty for score {score}"
        p = json.loads(raw)
        previews[score] = p
        assert p["settlement_payout_bps"] == expected_bucket, (
            f"score {score}: expected bucket {expected_bucket}, got {p['settlement_payout_bps']}"
        )
        # Settlement split must match the bucket exactly and conserve escrow.
        expected_agent = (ESCROW_WEI * expected_bucket) // 10000
        assert int(p["agent_payout_wei"]) == expected_agent
        assert int(p["client_refund_wei"]) == ESCROW_WEI - expected_agent
        assert int(p["agent_payout_wei"]) + int(p["client_refund_wei"]) == ESCROW_WEI
        assert p["passed"] is (expected_bucket > 0)

    # Fail band: agent gets nothing, client fully refunded.
    assert int(previews[4000]["agent_payout_wei"]) == 0
    assert int(previews[4000]["client_refund_wei"]) == ESCROW_WEI

    # Full band: agent gets everything.
    assert int(previews[9000]["agent_payout_wei"]) == ESCROW_WEI
    assert int(previews[9000]["client_refund_wei"]) == 0

    # Equivalence: distinct scores in the same bucket pay identically.
    assert previews[5000]["agent_payout_wei"] == previews[5999]["agent_payout_wei"]
    assert previews[7000]["agent_payout_wei"] == previews[7500]["agent_payout_wei"] == previews[7999]["agent_payout_wei"]

    # Reviewer scenario: a full-payout result can never equal a partial one.
    assert previews[8499]["settlement_payout_bps"] != previews[8500]["settlement_payout_bps"]
    assert int(previews[8500]["agent_payout_wei"]) == ESCROW_WEI
    assert 0 < int(previews[8499]["agent_payout_wei"]) < ESCROW_WEI


# ---------------------------------------------------------------------------
# Test 4: Task Indexing for Client and Agent
# ---------------------------------------------------------------------------

def test_task_indexing_for_parties(client, deployer, agent_account, contract_address, seeded_task):
    """Verify that get_client_tasks and get_agent_tasks list the seeded task."""
    client_tasks_raw = client.read_contract(
        address=contract_address,
        function_name="get_client_tasks",
        args=[deployer.address],
    )
    client_tasks = json.loads(client_tasks_raw)
    assert isinstance(client_tasks, list)
    assert seeded_task in client_tasks

    agent_tasks_raw = client.read_contract(
        address=contract_address,
        function_name="get_agent_tasks",
        args=[agent_account.address],
    )
    agent_tasks = json.loads(agent_tasks_raw)
    assert isinstance(agent_tasks, list)
    assert seeded_task in agent_tasks


# ---------------------------------------------------------------------------
# Test 5: Task Creation and State Verification
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
    assert int(task["min_threshold_bps"]) == MIN_THRESHOLD_BPS
    assert int(task["full_threshold_bps"]) == FULL_THRESHOLD_BPS


# ---------------------------------------------------------------------------
# Test 6: Task Cancellation and Escrow Refund Lifecycle
# ---------------------------------------------------------------------------

def test_cancel_task_lifecycle(client, agent_account, contract_address):
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
# Test 7: Escrow Accounting and Tracking
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
