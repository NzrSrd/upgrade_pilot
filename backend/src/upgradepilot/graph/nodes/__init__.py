"""Node bodies, one module per layer of spec 8's topology.

Split by layer rather than kept in one file because the layers have genuinely
different dependencies: `evidence` needs a workspace and a knowledge store,
`judgment` needs a model and the threshold table, `planning` needs neither of
the first two. A single module would import all of them for every node.

`base` holds what every layer shares -- the `traced` wrapper that enforces
CLAUDE.md rule 20 once for the whole graph rather than per node body.

Re-exported here so callers keep importing `upgradepilot.graph.nodes`, which
is the seam the graph builder and its tests already use.
"""

from upgradepilot.graph.nodes.base import (
    NodeBody,
    StateUpdate,
    make_stub,
    stub_node,
    traced,
)

__all__ = [
    "NodeBody",
    "StateUpdate",
    "make_stub",
    "stub_node",
    "traced",
]
