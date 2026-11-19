import random
import unittest

from vyron_api.graph import algorithms as alg


def adj(edges: list[tuple[str, str]], extra: tuple[str, ...] = ()) -> alg.Adjacency:
    nodes = {n for e in edges for n in e} | set(extra)
    return alg.undirected_adjacency(nodes, edges)


class AdjacencyTest(unittest.TestCase):
    def test_edges_are_undirected_and_self_loops_dropped(self) -> None:
        graph = adj([("a", "b"), ("b", "b")])
        self.assertEqual(graph, {"a": {"b"}, "b": {"a"}})

    def test_unknown_node_in_edge_raises(self) -> None:
        with self.assertRaises(KeyError):
            alg.undirected_adjacency(["a"], [("a", "z")])


class BfsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = adj([("a", "b"), ("b", "c"), ("c", "d"), ("a", "e")], extra=("lonely",))

    def test_depths_respect_max_depth(self) -> None:
        self.assertEqual(alg.bfs_depths(self.graph, "a", 1), {"a": 0, "b": 1, "e": 1})
        self.assertEqual(
            alg.bfs_depths(self.graph, "a", 9), {"a": 0, "b": 1, "e": 1, "c": 2, "d": 3}
        )

    def test_depth_zero_is_just_the_start(self) -> None:
        self.assertEqual(alg.bfs_depths(self.graph, "a", 0), {"a": 0})

    def test_errors(self) -> None:
        with self.assertRaises(KeyError):
            alg.bfs_depths(self.graph, "nope", 1)
        with self.assertRaises(ValueError):
            alg.bfs_depths(self.graph, "a", -1)


class ShortestPathTest(unittest.TestCase):
    def test_finds_fewest_hops(self) -> None:
        graph = adj([("a", "b"), ("b", "c"), ("c", "d"), ("a", "d")])
        self.assertEqual(alg.shortest_path(graph, "a", "d", 5), ["a", "d"])

    def test_ties_break_by_smallest_ids(self) -> None:
        graph = adj([("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])
        self.assertEqual(alg.shortest_path(graph, "a", "d", 5), ["a", "b", "d"])

    def test_same_node(self) -> None:
        self.assertEqual(alg.shortest_path(adj([("a", "b")]), "a", "a", 0), ["a"])

    def test_unreachable_and_too_far(self) -> None:
        graph = adj([("a", "b"), ("b", "c")], extra=("z",))
        self.assertIsNone(alg.shortest_path(graph, "a", "z", 9))
        self.assertIsNone(alg.shortest_path(graph, "a", "c", 1))
        self.assertEqual(alg.shortest_path(graph, "a", "c", 2), ["a", "b", "c"])

    def test_errors(self) -> None:
        graph = adj([("a", "b")])
        for args in (("x", "a", 1), ("a", "x", 1)):
            with self.subTest(args=args), self.assertRaises(KeyError):
                alg.shortest_path(graph, *args)
        with self.assertRaises(ValueError):
            alg.shortest_path(graph, "a", "b", -1)


class ComponentsTest(unittest.TestCase):
    def test_components_sorted_largest_first(self) -> None:
        graph = adj([("a", "b"), ("b", "c"), ("x", "y")], extra=("solo",))
        self.assertEqual(
            alg.connected_components(graph), [["a", "b", "c"], ["x", "y"], ["solo"]]
        )

    def test_empty_graph(self) -> None:
        self.assertEqual(alg.connected_components({}), [])


class PageRankTest(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(alg.pagerank({}), {})

    def test_sums_to_one_and_hub_wins(self) -> None:
        ranks = alg.pagerank({"a": ["hub"], "b": ["hub"], "c": ["hub"], "hub": []})
        self.assertAlmostEqual(sum(ranks.values()), 1.0, places=9)
        self.assertEqual(max(ranks, key=lambda n: ranks[n]), "hub")
        self.assertAlmostEqual(ranks["a"], ranks["b"], places=12)

    def test_symmetric_cycle_is_uniform(self) -> None:
        ranks = alg.pagerank({"a": ["b"], "b": ["c"], "c": ["a"]})
        for value in ranks.values():
            self.assertAlmostEqual(value, 1 / 3, places=9)

    def test_unknown_targets_and_self_links_are_ignored(self) -> None:
        ranks = alg.pagerank({"a": ["ghost", "a"], "b": []})
        self.assertAlmostEqual(sum(ranks.values()), 1.0, places=9)
        self.assertAlmostEqual(ranks["a"], ranks["b"], places=9)

    def test_invalid_damping(self) -> None:
        for damping in (0.0, 1.0, -0.1, 2.0):
            with self.subTest(damping=damping), self.assertRaises(ValueError):
                alg.pagerank({"a": []}, damping=damping)


class CommunitiesTest(unittest.TestCase):
    EDGES = [
        ("a", "b"), ("a", "c"), ("a", "d"), ("b", "c"), ("b", "d"), ("c", "d"),
        ("e", "f"), ("e", "g"), ("e", "h"), ("f", "g"), ("f", "h"), ("g", "h"),
        ("d", "e"),
    ]  # fmt: skip

    def test_two_cliques_joined_by_a_bridge(self) -> None:
        result = alg.modularity_communities(adj(self.EDGES))
        self.assertEqual({result[n] for n in "abcd"}, {result["a"]})
        self.assertEqual({result[n] for n in "efgh"}, {result["e"]})
        self.assertNotEqual(result["a"], result["e"])

    def test_result_does_not_depend_on_input_order(self) -> None:
        baseline = alg.modularity_communities(adj(self.EDGES))
        for seed in range(5):
            shuffled = self.EDGES[:]
            random.Random(seed).shuffle(shuffled)
            flipped = [(b, a) if seed % 2 else (a, b) for a, b in shuffled]
            self.assertEqual(alg.modularity_communities(adj(flipped)), baseline)

    def test_isolated_nodes_are_their_own_community(self) -> None:
        result = alg.modularity_communities(adj([("a", "b")], extra=("z",)))
        self.assertEqual(result["z"], "z")

    def test_edgeless_graph(self) -> None:
        self.assertEqual(
            alg.modularity_communities({"a": set(), "b": set()}), {"a": "a", "b": "b"}
        )


if __name__ == "__main__":
    unittest.main()
