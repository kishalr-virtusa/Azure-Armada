"""Shared test fixtures: an in-memory Cosmos DB for dr_agent.tools.store."""
import copy
import itertools
import re

import pytest
from azure.cosmos.exceptions import CosmosResourceExistsError, CosmosResourceNotFoundError

from dr_agent.tools import store

# The only query shapes store.py issues; anything else fails loudly so a new query gets a test.
_QUERY = re.compile(r"SELECT (?:TOP (?P<top>\d+) )?(?P<proj>VALUE COUNT\(1\)|\*|c\.\w+(?:, c\.\w+)*) FROM c"
                    r"(?: WHERE c\.id (?P<op>=|>=|>) (?P<rhs>@\w+|'[^']*'))?\s*(?:ORDER BY c\.id (?P<order>ASC|DESC))?")
_OPS = {"=": lambda a, b: a == b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}
_etag = itertools.count(1)


class FakeContainer:
    """Just enough of azure.cosmos ContainerProxy. Stored documents carry system fields like the real service."""

    def __init__(self):
        self.docs: dict[tuple[str, str], dict] = {}

    def _put(self, body):
        doc = copy.deepcopy(body)
        doc.update(_rid="r", _self="s", _etag=f'"{next(_etag)}"', _attachments="a/", _ts=1)
        self.docs[(doc["pk"], doc["id"])] = doc
        return copy.deepcopy(doc)

    def read_item(self, item, partition_key):
        try:
            return copy.deepcopy(self.docs[(partition_key, item)])
        except KeyError:
            raise CosmosResourceNotFoundError(message="not found") from None

    def create_item(self, body):
        if (body["pk"], body["id"]) in self.docs:
            raise CosmosResourceExistsError(message="conflict")
        return self._put(body)

    def upsert_item(self, body):
        return self._put(body)

    def patch_item(self, item, partition_key, patch_operations):
        assert len(patch_operations) <= 10, "Cosmos DB allows at most 10 operations per patch"
        doc = self.read_item(item, partition_key)
        for op in patch_operations:
            assert op["op"] == "set"
            doc[op["path"][1:].replace("~1", "/").replace("~0", "~")] = op["value"]
        return self._put(doc)

    def delete_item(self, item, partition_key):
        self.read_item(item, partition_key)
        del self.docs[(partition_key, item)]

    def query_items(self, query, parameters=None, partition_key=None, enable_cross_partition_query=False):
        m = _QUERY.fullmatch(query.strip())
        assert m, f"query shape not supported by the fake: {query}"
        assert partition_key is not None or enable_cross_partition_query, "cross-partition query not enabled"
        params = {p["name"]: p["value"] for p in parameters or []}
        rows = [d for (pk, _), d in sorted(self.docs.items()) if partition_key is None or pk == partition_key]
        if m["op"]:
            rhs = params[m["rhs"]] if m["rhs"].startswith("@") else m["rhs"].strip("'")
            rows = [d for d in rows if _OPS[m["op"]](d["id"], rhs)]
        if m["order"]:
            rows.sort(key=lambda d: d["id"], reverse=m["order"] == "DESC")
        if m["top"]:
            rows = rows[:int(m["top"])]
        if m["proj"] == "VALUE COUNT(1)":
            return [len(rows)]
        if m["proj"] == "*":
            return [copy.deepcopy(d) for d in rows]
        fields = [f[2:] for f in m["proj"].split(", ")]
        return [{f: d[f] for f in fields if f in d} for d in rows]  # missing fields are omitted, as in Cosmos


@pytest.fixture
def cosmos(monkeypatch):
    """store.py backed by in-memory containers; returns {container name: FakeContainer}."""
    db: dict[str, FakeContainer] = {}
    monkeypatch.setattr(store, "_container", lambda name: db.setdefault(name, FakeContainer()))
    return db
