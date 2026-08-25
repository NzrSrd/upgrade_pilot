"""What the graph needs from the outside world, in one object.

The graph's nodes reach three things that live longer than a run and cannot
be constructed inside one: a chat model wrapper that records usage, a
knowledge store holding an open Chroma client, and a workspace manager
holding the repository-access policy. All three are owned by whoever owns the
process -- Phase 9's FastAPI lifespan, a `with` block in a test -- and are
injected here rather than built by `build_graph`.

Bundled into one frozen object rather than passed as four keyword arguments
for a specific reason: every later phase adds a node with its own
dependencies, and a builder signature that grows a parameter per phase makes
each of those phases touch every call site. A dependency added to this class
reaches the builder without changing how anyone calls it.

Deliberately **not** `Settings`. The graph needs two numbers out of the
settings object and nothing else; handing it the whole thing would let any
node read any configuration value, and the layering rule (CLAUDE.md rule 16)
that keeps `graph/` from knowing about HTTP is worth the same discipline
about configuration. The two numbers are named here, so a node that wants a
third has to say so.
"""

from dataclasses import dataclass

from upgradepilot.services.knowledge.store import DEFAULT_LIMIT, KnowledgeStore
from upgradepilot.services.llm.tracked import TrackedLLM
from upgradepilot.services.repo.manager import WorkspaceManager


@dataclass(frozen=True, slots=True)
class GraphDeps:
    """Everything the assembled graph needs, injected once at build time."""

    llm: TrackedLLM
    store: KnowledgeStore
    workspaces: WorkspaceManager

    max_rag_iterations: int = 3
    """Spec 7.3's loop bound. Defaults to the shipped `Settings` value rather
    than reading it, so a test can shorten the loop without a settings
    object -- and so that a zero, which stops the loop before its first
    evaluation, is expressible."""

    retrieval_limit: int = DEFAULT_LIMIT
    """Chunks per query. Defaults to the store's own limit, which is the
    number the golden set's recall@5 floor is measured at -- a graph
    retrieving a different number would be scored by a floor that does not
    describe it."""
