import importlib

from optimizer.action_registry import generate_inventory_actions, registry_systems
from optimizer.coverage import MAJOR_SYSTEMS, coverage_report
from optimizer.gpu_batch_engine import GpuBatchEngine
from optimizer.knowledge_loader import load_knowledge
from optimizer.numeric_features import score_matrix_cpu
from optimizer.numeric_state import KnowledgeIndex, state_vector, to_numeric_state
from optimizer.player_state import PlayerState
from tools.audit_inventory_actions import audit
from tools.audit_item_affordances import audit_affordances
from tools.device_utils import cuda_available
from tools.npu_probe import probe
from tools.profile_batch_generator import AsyncProfileProducer, ProfileBatchGenerator


GENERATOR_MODULES = [
    "resources", "cores", "chests", "selectors", "gear", "ss_gear", "pets", "xeno_pets",
    "tech_parts", "resonance", "collectibles", "collectible_sets", "survivors", "survivor_awakening",
    "events", "event_shops", "clan_shop", "exchanges", "universal_exchange", "merge", "salvage", "save_hold",
    "skills", "pet_merging", "pet_awakenings",
]


def test_all_major_action_generator_modules_import_and_register():
    for name in GENERATOR_MODULES:
        importlib.import_module(f"optimizer.action_generators.{name}")
    assert set(MAJOR_SYSTEMS) <= set(registry_systems())


def test_affordance_and_inventory_audits_report_major_metrics():
    affordances = audit_affordances()
    inventory = audit()
    assert "coverage_percent" in affordances
    assert set(MAJOR_SYSTEMS) <= set(inventory["systems_implemented"])
    assert "actions_by_system" in inventory
    assert "inventory_action_coverage_percent" in inventory


def test_numeric_state_and_cpu_gpu_scores_match():
    knowledge = load_knowledge()
    state = PlayerState(resources={"astral_core": 2}, inventory={"core_selector_chests": 1})
    index = KnowledgeIndex.from_knowledge(knowledge)
    numeric = to_numeric_state(state, index)
    assert len(state_vector(numeric)) >= len(index.ids)
    matrix = [[1.0, 2.0, 3.0], [2.0, 1.0, 0.5]]
    weights = [0.5, 1.5, 2.0]
    expected = score_matrix_cpu(matrix, weights)
    engine = GpuBatchEngine("cuda" if cuda_available() else "cpu")
    actual, stats = engine.score(matrix, weights)
    assert max(abs(left - right) for left, right in zip(expected, actual)) < 1e-4
    assert stats["gpu_used"] is cuda_available()


def test_profile_seed_generation_stays_cpu_only_and_uses_known_ids():
    generator = ProfileBatchGenerator(42, gpu_features=True, device="auto")
    batch = generator.numeric_batch(16)
    profiles = generator.materialize(batch)
    pools = generator.pools
    known_owned = set().union(*(set(values) for values in pools.values()))
    assert len(profiles) == 16
    assert all(set(profile["player_state"]["owned_items"]) <= known_owned for profile in profiles)
    assert batch["device"] == "cpu"
    assert batch["gpu_used"] is False


def test_async_profile_pipeline_drains_bounded_queue():
    generator = ProfileBatchGenerator(43, gpu_features=False, device="cpu")
    producer = AsyncProfileProducer(generator, batch_size=8, queue_size=1)
    producer.start()
    batch = producer.get(timeout=5)
    profiles = generator.materialize(batch)
    status = producer.close()
    assert len(profiles) == 8
    assert status["queue_capacity"] == 1
    assert not status["errors"]


def test_npu_probe_never_claims_active_without_model():
    status = probe()
    assert status["active"] is False
    assert "idle_reason" in status


def test_combo_and_shop_actions_use_real_known_ids_only():
    knowledge = {
        "items": [], "resources": [{"id": "event_currency", "name": "Event Currency", "description": "event currency"}],
        "chests": [{"id": "pet_selector", "name": "Pet Selector", "description": "pet selector", "choices": ["pet_copy"]}],
        "gear": [], "pets": [{"id": "pet_copy", "name": "Pet Copy", "description": "pet awakening"}], "xeno_pets": [],
        "tech_parts": [{"id": "tech_part", "name": "Tech", "description": "tech resonance"}],
        "collectibles": [{"id": "collectible", "name": "Collectible", "description": "collectible set"}],
        "survivors": [{"id": "survivor_shard", "name": "Survivor Shard", "description": "survivor awakening shard"}],
        "survivor_awakenings": [], "events": [],
        "event_shops": [{"id": "shop_core", "name": "Shop Core", "description": "core", "cost": {"event_currency": 2}}],
        "clan_shop": [], "universal_exchange": [], "warnings": [],
    }
    state = PlayerState(resources={"event_currency": 3}, inventory={"items": {"pet_selector": 1, "pet_copy": 2, "tech_part": 2, "collectible": 2, "survivor_shard": 2}})
    actions = generate_inventory_actions(state, knowledge, max_actions=500)
    action_ids = " ".join(action["action_id"] for action in actions)
    assert "pet_copy" in action_ids
    assert "collectible" in action_ids
    assert "tech_part" in action_ids
    assert "survivor_shard" in action_ids
    assert any(action["system"] == "event_shops" and action["supported"] for action in actions)
    known = {"event_currency", "pet_selector", "pet_copy", "tech_part", "collectible", "survivor_shard", "shop_core"}
    assert all(str(action.get("metadata", {}).get("item_id", "")) in known for action in actions if action.get("metadata", {}).get("item_id"))


def test_real_coverage_exposes_missing_data_instead_of_fake_coverage():
    knowledge = load_knowledge()
    state = PlayerState(resources={record.id: 2 for record in knowledge["resources"]}, inventory={"core_selector_chests": 1})
    report = coverage_report(knowledge, state)
    assert set(MAJOR_SYSTEMS) <= set(report["systems_implemented"])
    assert "systems_missing_data" in report
