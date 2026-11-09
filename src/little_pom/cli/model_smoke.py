"""One explicit paid synthetic sample, isolated from experimental samples."""
from little_pom.contracts import get_schema
from little_pom.runtime import AdaptionBackend, Ledger, SwarmClient


def model_smoke(args):
    if not args.live or args.secret_path is None:
        raise ValueError('model-smoke requires --live and an explicit --secret-path')
    if (args.run_dir / 'manifests').exists():
        raise ValueError('Use a separate run directory for transport smoke tests')
    request = {
        'model': 'qwen3.5-397b', 'config': {}, 'history': [], 'output_budget': 1024,
        'instructions': 'This is a synthetic transport test, not an experimental review. '
                        'Return only one JSON object matching the supplied schema. '
                        'Set status to no_claim, claim to an empty string, evidence_ids to [], '
                        'and decision_relevance_text to Synthetic transport test only. '
                        'Set schema_version to 1.0 and id to smoke. No markdown.',
        'packet': {'schema': get_schema('Review')}, 'stage': 'transport-smoke',
    }
    client = SwarmClient(Ledger(args.run_dir), AdaptionBackend(args.secret_path))
    return client.call(request, 'Review', 'synthetic-transport-smoke-v1')
