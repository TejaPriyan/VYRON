"""Request limits shared by the contract, the parameter parser and the services."""

from __future__ import annotations

API_VERSION = "v1"
API_PREFIX = f"/api/{API_VERSION}"

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
MAX_CURSOR_LENGTH = 512

MAX_GRAPH_DEPTH = 3  # hops from the centre entity
DEFAULT_GRAPH_DEPTH = 1
MAX_GRAPH_NODES = 300  # a browser should never be sent the whole graph
DEFAULT_GRAPH_NODES = 100
MAX_GRAPH_EDGES = 1000  # relationships returned with one neighbourhood
MAX_CLUSTER_MEMBERS = 300  # member ids listed for one cluster; must equal providers.graph.MAX_CLUSTER_MEMBERS
MAX_PATH_DEPTH = 8
DEFAULT_PATH_DEPTH = 6

MAX_QUERY_LENGTH = 100  # search text
MAX_QUERY_STRING_BYTES = 2048
MAX_BODY_BYTES = 64 * 1024
MIN_YEAR = 1900
MAX_YEAR = 2200
