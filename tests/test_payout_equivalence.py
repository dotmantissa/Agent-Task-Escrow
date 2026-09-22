"""
Payout-Equivalence Invariant Tests (network-free, deterministic)
================================================================

These tests are the direct, fast proof that the reviewer's requirement is met:

    "redesign validation so every accepted validator result resolves to the
     same payout amount or payout bucket"

They exercise the REAL settlement logic in ``contracts/agent_task_escrow.py``
(the pure methods ``_quantize_payout_bps`` and ``_settlement_amounts``) without a
network or an LLM. The GenLayer SDK is stubbed with lightweight identity types so
the contract module can be imported and the pure methods called directly; the
functions under test use only builtins (``int``/``max``/``min``/``//``), so the
behavior observed here is exactly the behavior on-chain.

The consensus gate in ``submit_deliverable`` accepts a validator ONLY when
``leader_payout == mine["payout_bps"]`` (exact equality, no tolerance band). Since
settlement is a pure function of ``payout_bps``, proving the properties of the
quantizer + settlement split proves the end-to-end invariant:

  * two scores in the SAME bucket  -> identical payout_bps -> identical wei paid
    (accepted results are payout-equivalent), and
  * two scores in DIFFERENT buckets -> different payout_bps -> consensus rejects
    (materially different payments can never both be accepted), and
  * a full-payout bucket (10000) is only ever produced by score >= full_threshold,
    so it can never validate against a partial-payout bucket.
"""

import importlib.util
import os
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Import the real contract module with a stubbed `genlayer` SDK.
# ---------------------------------------------------------------------------

def _install_fake_genlayer():
    """Register a minimal `genlayer` module sufficient to DEFINE the contract class.

    Only names referenced at import / class-definition time need to exist. The
    pure methods we test use no SDK features, so nothing here influences their
    behavior — it only lets the module load under a plain CPython interpreter.
    """
    m = types.ModuleType("genlayer")

    # Storage scalar types are only used as annotations / constructors elsewhere;
    # identity-to-int is a faithful stand-in for the arithmetic we test.
    m.u256 = int
    m.u64 = int
    m.u32 = int
    m.i256 = int

    class Address:
        def __init__(self, *args, **kwargs):
            pass

    m.Address = Address

    class _Subscriptable:
        def __class_getitem__(cls, _item):
            return cls

    class TreeMap(_Subscriptable):
        pass

    class DynArray(_Subscriptable):
        pass

    m.TreeMap = TreeMap
    m.DynArray = DynArray

    def allow_storage(cls):
        return cls

    m.allow_storage = allow_storage

    # `gl` namespace: only decorators + base class + a couple of names touched at
    # definition time are required.
    def _identity_decorator(fn):
        return fn

    class _WriteDecorator:
        def __call__(self, fn):
            return fn

        def payable(self, fn):
            return fn

    class _Public:
        write = _WriteDecorator()

        @staticmethod
        def view(fn):
            return fn

    class _VM:
        class UserError(Exception):
            pass

        class Return:
            pass

        @staticmethod
        def run_nondet_unsafe(_leader, _validator):
            raise RuntimeError("run_nondet_unsafe is not exercised by these tests")

    class _GL:
        Contract = object
        public = _Public()
        vm = _VM

    m.gl = _GL()

    sys.modules["genlayer"] = m
    return m


def _load_contract_module():
    _install_fake_genlayer()
    here = os.path.dirname(os.path.abspath(__file__))
    contract_path = os.path.join(here, "..", "contracts", "agent_task_escrow.py")
    contract_path = os.path.normpath(contract_path)
    spec = importlib.util.spec_from_file_location("agent_task_escrow_undertest", contract_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CONTRACT = _load_contract_module()
AgentTaskEscrow = _CONTRACT.AgentTaskEscrow
PARTIAL_PAYOUT_STEP_BPS = _CONTRACT.PARTIAL_PAYOUT_STEP_BPS


@pytest.fixture(scope="module")
def contract():
    # Bypass __init__ (which touches gl.message); we only call pure methods.
    return object.__new__(AgentTaskEscrow)


def quantize(contract, score, tmin, tfull):
    return contract._quantize_payout_bps(score, tmin, tfull)


def settle(contract, escrow, payout_bps):
    return contract._settlement_amounts(escrow, payout_bps)


# Threshold configurations exercised. The first matches the integration suite.
THRESHOLDS = [
    (5000, 8500),
    (3000, 9000),
    (100, 10000),
    (5000, 5000),   # degenerate: no partial band (min == full)
]

ESCROW = 10 ** 16  # 0.01 GEN, same as the integration suite


# ---------------------------------------------------------------------------
# 1. Determinism: identical inputs always produce identical outputs.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tmin,tfull", THRESHOLDS)
def test_quantizer_is_deterministic(contract, tmin, tfull):
    for score in range(0, 10001, 137):
        a = quantize(contract, score, tmin, tfull)
        b = quantize(contract, score, tmin, tfull)
        assert a == b


# ---------------------------------------------------------------------------
# 2. Fail band -> 0 payout (full client refund); full band -> 10000 (full payout).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tmin,tfull", THRESHOLDS)
def test_fail_band_pays_zero(contract, tmin, tfull):
    for score in range(0, tmin):
        assert quantize(contract, score, tmin, tfull) == 0
    agent, refund = settle(contract, ESCROW, 0)
    assert agent == 0
    assert refund == ESCROW


@pytest.mark.parametrize("tmin,tfull", THRESHOLDS)
def test_full_band_pays_everything(contract, tmin, tfull):
    for score in range(tfull, 10001):
        assert quantize(contract, score, tmin, tfull) == 10000
    agent, refund = settle(contract, ESCROW, 10000)
    assert agent == ESCROW
    assert refund == 0


# ---------------------------------------------------------------------------
# 3. THE core invariant: every score inside one bucket resolves to the SAME
#    payout_bps AND the SAME wei split. This is exactly "every accepted result
#    resolves to the same payout amount": accepted == same bucket == same money.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tmin,tfull", THRESHOLDS)
def test_scores_in_same_bucket_pay_identically(contract, tmin, tfull):
    buckets = {}
    for score in range(0, 10001):
        p = quantize(contract, score, tmin, tfull)
        buckets.setdefault(p, []).append(score)

    for payout_bps, scores in buckets.items():
        # Same discretized decision -> byte-identical settlement for every score.
        splits = {settle(contract, ESCROW, quantize(contract, s, tmin, tfull)) for s in scores}
        assert len(splits) == 1, (
            f"bucket {payout_bps} produced multiple settlements {splits}"
        )
        # And the settlement matches the bucket's own split.
        assert next(iter(splits)) == settle(contract, ESCROW, payout_bps)


# ---------------------------------------------------------------------------
# 4. Reviewer scenario A ("weighted scores up to 10 percentage points apart"):
#    two scores ~10pp apart that the OLD 1000-bps tolerance would have accepted
#    now land in DIFFERENT buckets, so the exact-match gate rejects them.
# ---------------------------------------------------------------------------

def test_ten_point_apart_scores_now_reject(contract):
    tmin, tfull = 5000, 8500
    leader_score = 8499   # partial, just under full threshold
    validator_score = 7500  # ~10pp lower, still partial
    assert abs(leader_score - validator_score) <= 1000  # OLD tolerance WOULD accept
    lp = quantize(contract, leader_score, tmin, tfull)
    vp = quantize(contract, validator_score, tmin, tfull)
    assert lp != vp, "materially different scores must not share a bucket"
    # Exact-match consensus gate (leader_payout == mine) would therefore reject.
    assert (lp == vp) is False
    # And the payouts really are materially different (10% of escrow apart).
    la, _ = settle(contract, ESCROW, lp)
    va, _ = settle(contract, ESCROW, vp)
    assert la != va


# ---------------------------------------------------------------------------
# 5. Reviewer scenario B ("a full-payout result to validate against partial
#    compliance"): a CLEAR_PASS (full) bucket can never equal a partial bucket.
# ---------------------------------------------------------------------------

def test_full_payout_never_matches_partial(contract):
    tmin, tfull = 5000, 8500
    full_score = 8500       # exactly the full threshold -> full payout bucket
    partial_score = 8499    # one bps below -> partial bucket
    full_payout = quantize(contract, full_score, tmin, tfull)
    partial_payout = quantize(contract, partial_score, tmin, tfull)
    assert full_payout == 10000
    assert 0 < partial_payout < 10000
    assert full_payout != partial_payout  # exact-match gate rejects this pairing


@pytest.mark.parametrize("tmin,tfull", THRESHOLDS)
def test_full_bucket_only_from_full_scores(contract, tmin, tfull):
    # The 10000 (full-payout) bucket is produced ONLY by scores >= full_threshold.
    for score in range(0, tfull):
        assert quantize(contract, score, tmin, tfull) < 10000


# ---------------------------------------------------------------------------
# 6. Partial payouts are strictly inside (0, 10000): partial work earns partial
#    pay, but a partial score can neither be zeroed nor silently promoted to full.
# ---------------------------------------------------------------------------

def test_partial_band_is_strictly_bounded(contract):
    tmin, tfull = 5000, 8500
    for score in range(tmin, tfull):
        p = quantize(contract, score, tmin, tfull)
        assert 0 < p < 10000
        assert p % PARTIAL_PAYOUT_STEP_BPS == 0 or p == tmin
        assert p >= tmin


# ---------------------------------------------------------------------------
# 7. Monotonicity: a higher score never pays the agent less.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tmin,tfull", THRESHOLDS)
def test_payout_is_monotonic_non_decreasing(contract, tmin, tfull):
    prev = -1
    for score in range(0, 10001):
        p = quantize(contract, score, tmin, tfull)
        assert p >= prev
        prev = p


# ---------------------------------------------------------------------------
# 8. Settlement conservation: agent payout + client refund == escrow, always,
#    with both sides within bounds. No wei is created or destroyed.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("escrow", [1, 3, 10 ** 16, 10 ** 18 + 7, 12345])
def test_settlement_conserves_escrow(contract, escrow):
    for payout_bps in range(0, 10001, 250):
        agent, refund = settle(contract, escrow, payout_bps)
        assert agent + refund == escrow
        assert 0 <= agent <= escrow
        assert 0 <= refund <= escrow


# ---------------------------------------------------------------------------
# 9. Concrete ladder for the integration-suite thresholds (documents exact wei).
# ---------------------------------------------------------------------------

def test_concrete_bucket_ladder(contract):
    tmin, tfull = 5000, 8500
    expected = {
        4000: 0,
        5000: 5000,
        5999: 5000,
        6000: 6000,
        7000: 7000,
        7500: 7000,   # the value the old proportional test asserted as 7500
        7999: 7000,
        8000: 8000,
        8499: 8000,
        8500: 10000,
        9000: 10000,
        10000: 10000,
    }
    for score, want in expected.items():
        assert quantize(contract, score, tmin, tfull) == want, f"score {score}"

    # 7500 now settles from the 7000 bucket, not a proportional 7500.
    agent, refund = settle(contract, ESCROW, quantize(contract, 7500, tmin, tfull))
    assert agent == (ESCROW * 7000) // 10000
    assert refund == ESCROW - agent


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
