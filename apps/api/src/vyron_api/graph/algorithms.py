"""Pure graph algorithms over plain dicts. No I/O, deterministic, standard library only.

Node ids are strings. Undirected adjacency is ``dict[node, set[neighbor]]``; every neighbor is also
a key. All iteration is over sorted ids, so results do not depend on insertion order.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence

Adjacency = dict[str, set[str]]


def undirected_adjacency(node_ids: Iterable[str], edges: Iterable[tuple[str, str]]) -> Adjacency:
    adjacency: Adjacency = {node: set() for node in node_ids}
    for source, target in edges:
        if source == target:
            continue
        if source not in adjacency or target not in adjacency:
            raise KeyError(f"edge references unknown node: {source!r} -> {target!r}")
        adjacency[source].add(target)
        adjacency[target].add(source)
    return adjacency


def bfs_depths(adjacency: Mapping[str, set[str]], start: str, max_depth: int) -> dict[str, int]:
    """Depth of every node within ``max_depth`` hops of ``start``, in discovery order."""
    if start not in adjacency:
        raise KeyError(start)
    if max_depth < 0:
        raise ValueError("max_depth must not be negative")
    depths = {start: 0}
    queue: deque[str] = deque([start])
    while queue:
        node = queue.popleft()
        if depths[node] == max_depth:
            continue
        for neighbor in sorted(adjacency[node]):
            if neighbor not in depths:
                depths[neighbor] = depths[node] + 1
                queue.append(neighbor)
    return depths


def shortest_path(adjacency: Mapping[str, set[str]], source: str, target: str, max_depth: int) -> list[str] | None:
    """Fewest-hop path (ties broken by smallest ids), or None if none within ``max_depth``."""
    if source not in adjacency:
        raise KeyError(source)
    if target not in adjacency:
        raise KeyError(target)
    if max_depth < 0:
        raise ValueError("max_depth must not be negative")
    if source == target:
        return [source]
    parents: dict[str, str] = {}
    depths = {source: 0}
    queue: deque[str] = deque([source])
    while queue:
        node = queue.popleft()
        if depths[node] == max_depth:
            continue
        for neighbor in sorted(adjacency[node]):
            if neighbor in depths:
                continue
            depths[neighbor] = depths[node] + 1
            parents[neighbor] = node
            if neighbor == target:
                path = [target]
                while path[-1] != source:
                    path.append(parents[path[-1]])
                return path[::-1]
            queue.append(neighbor)
    return None


def connected_components(adjacency: Mapping[str, set[str]]) -> list[list[str]]:
    """Components as sorted id lists, largest first (ties by smallest id)."""
    seen: set[str] = set()
    components: list[list[str]] = []
    for node in sorted(adjacency):
        if node in seen:
            continue
        stack = [node]
        seen.add(node)
        members: list[str] = []
        while stack:
            current = stack.pop()
            members.append(current)
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        components.append(sorted(members))
    components.sort(key=lambda members: (-len(members), members[0]))
    return components


def pagerank(
    out_links: Mapping[str, Sequence[str]],
    *,
    damping: float = 0.85,
    max_iterations: int = 200,
    tolerance: float = 1e-12,
) -> dict[str, float]:
    """PageRank over directed links. Dangling nodes spread their rank uniformly. Sums to 1."""
    if not 0.0 < damping < 1.0:
        raise ValueError("damping must be strictly between 0 and 1")
    nodes = sorted(out_links)
    count = len(nodes)
    if count == 0:
        return {}
    known = set(nodes)
    targets = {node: sorted({t for t in out_links[node] if t in known and t != node}) for node in nodes}
    rank = dict.fromkeys(nodes, 1.0 / count)
    for _ in range(max_iterations):
        dangling = sum(rank[node] for node in nodes if not targets[node])
        base = (1.0 - damping) / count + damping * dangling / count
        updated = dict.fromkeys(nodes, base)
        for node in nodes:
            if targets[node]:
                share = damping * rank[node] / len(targets[node])
                for target in targets[node]:
                    updated[target] += share
        delta = sum(abs(updated[node] - rank[node]) for node in nodes)
        rank = updated
        if delta < tolerance:
            break
    return rank


def modularity_communities(adjacency: Mapping[str, set[str]], *, max_passes: int = 50) -> dict[str, str]:
    """Greedy modularity local-moving (the first phase of Louvain), fully deterministic.

    Returns ``node -> community id`` where the id is the smallest-id node that seeded the community
    at the end. Every accepted move strictly increases modularity, so it terminates.
    """
    nodes = sorted(adjacency)
    degree = {node: len(adjacency[node] - {node}) for node in nodes}
    two_m = sum(degree.values())
    community = {node: node for node in nodes}
    if two_m == 0:
        return community
    total = dict(degree)
    for _ in range(max_passes):
        moved = False
        for node in nodes:
            k = degree[node]
            links: dict[str, int] = {}
            for neighbor in adjacency[node]:
                if neighbor != node:
                    links[community[neighbor]] = links.get(community[neighbor], 0) + 1
            current = community[node]
            total[current] -= k
            best = current
            best_gain = links.get(current, 0) - total[current] * k / two_m
            for candidate in sorted(links):
                gain = links[candidate] - total[candidate] * k / two_m
                if gain > best_gain + 1e-12:
                    best, best_gain = candidate, gain
            total[best] += k
            if best != current:
                community[node] = best
                moved = True
        if not moved:
            break
    members: dict[str, list[str]] = {}
    for node in nodes:
        members.setdefault(community[node], []).append(node)
    return {node: min(group) for group in members.values() for node in group}
