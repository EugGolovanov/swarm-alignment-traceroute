"""Crash-safe internal-model transport and immutable host-side artifacts.

SwarmClient returns the parsed provider object as ServiceResult.data. Runtime
metadata remains in Ledger.lookup(execution_id), never in reviewer packets.
Construct live backends only for explicitly authorized commands; MockBackend
provides entirely offline scripted transport responses.
"""
from .backends import AdaptionBackend, MockBackend, Redactor, SimulatedCrash
from .client import SwarmClient
from .subscription import OmpSubscriptionBackend
from .storage import ArtifactStore, FreezeStore, Ledger

__all__ = ['AdaptionBackend', 'ArtifactStore', 'FreezeStore', 'Ledger',
           'MockBackend', 'OmpSubscriptionBackend', 'Redactor', 'SimulatedCrash', 'SwarmClient']
