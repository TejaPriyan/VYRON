import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { addRead, emptyGraph } from "../../../../packages/graph/src/dependency-graph.ts";
import type { PackageRead } from "../../../../packages/graph/src/dependency-graph.ts";
import {
  describeGraph,
  describeLeftOut,
  describeNode,
  describeNotDrawn,
  describePackageCache,
  describeRead,
  describeRequirement,
  githubRepoFromUrl,
  isStandIn,
  MAX_NAME_LENGTH,
  packageFailure,
  packageLabel,
  readPackageParam,
  requestFromInput,
  statusHelp,
  STATUS_WORDS,
  writePackageParam,
} from "./dependencies-model.ts";

const problem = (result: ReturnType<typeof requestFromInput>): string => ("problem" in result ? result.problem : "");
const request = (result: ReturnType<typeof requestFromInput>): unknown => ("request" in result ? result.request : null);

describe("requestFromInput", () => {
  it("uses the registry that is picked for a plain name", () => {
    assert.deepEqual(request(requestFromInput("pypi", "  fixture-web ")), { registry: "pypi", name: "fixture-web" });
    assert.deepEqual(request(requestFromInput("npm", "fixture-server")), { registry: "npm", name: "fixture-server" });
  });

  it("lets a typed prefix choose the registry over the picker", () => {
    assert.deepEqual(request(requestFromInput("pypi", "npm:fixture-server")), { registry: "npm", name: "fixture-server" });
    assert.deepEqual(request(requestFromInput("npm", "PyPI:fixture-web")), { registry: "pypi", name: "fixture-web" });
  });

  it("reads a PyPI project link", () => {
    assert.deepEqual(request(requestFromInput("npm", "https://pypi.org/project/fixture-web/")), { registry: "pypi", name: "fixture-web" });
    assert.deepEqual(request(requestFromInput("npm", "https://pypi.org/project/fixture-web/1.2.3/")), { registry: "pypi", name: "fixture-web" });
  });

  it("reads an npm package link, scoped or not", () => {
    assert.deepEqual(request(requestFromInput("pypi", "https://www.npmjs.com/package/fixture-server")), { registry: "npm", name: "fixture-server" });
    assert.deepEqual(request(requestFromInput("pypi", "https://www.npmjs.com/package/@fixture-scope/types")), {
      registry: "npm",
      name: "@fixture-scope/types",
    });
    assert.deepEqual(request(requestFromInput("pypi", "https://www.npmjs.com/package/%40fixture-scope%2Ftypes")), { registry: "npm", name: "@fixture-scope/types" });
  });

  it("keeps a scoped npm name whole", () => {
    assert.deepEqual(request(requestFromInput("npm", "@fixture-scope/types")), { registry: "npm", name: "@fixture-scope/types" });
  });

  it("does not take a link to some other site for a package", () => {
    for (const text of ["https://example.com/project/fixture-web", "https://pypi.org/simple/fixture-web/", "ftp://pypi.org/project/x"]) {
      assert.match(problem(requestFromInput("pypi", text)), /not to a PyPI or npm package page/, text);
    }
  });

  it("reads a link pasted without https://", () => {
    assert.deepEqual(request(requestFromInput("npm", "pypi.org/project/fixture-web")), { registry: "pypi", name: "fixture-web" });
    assert.deepEqual(request(requestFromInput("pypi", "npmjs.com/package/fixture-server")), { registry: "npm", name: "fixture-server" });
  });

  it("asks for a name when there is none", () => {
    assert.match(problem(requestFromInput("pypi", "   ")), /Type a package name/);
    assert.match(problem(requestFromInput("npm", "npm:")), /after npm:/);
    assert.match(problem(requestFromInput("npm", "pypi:  ")), /after PyPI:/);
  });

  it("refuses spaces, quotes and markup characters", () => {
    for (const text of ["two words", 'quo"te', "it's", "<script>", "back\\slash", "tab\tname"]) {
      assert.match(problem(requestFromInput("pypi", text)), /no spaces or quotes/, text);
    }
  });

  it("refuses a name longer than the registries allow", () => {
    assert.match(problem(requestFromInput("pypi", "a".repeat(MAX_NAME_LENGTH + 1))), /too long/);
    assert.deepEqual(request(requestFromInput("pypi", "a".repeat(MAX_NAME_LENGTH))), { registry: "pypi", name: "a".repeat(MAX_NAME_LENGTH) });
  });
});

describe("the address", () => {
  it("reads ?package=registry:name", () => {
    assert.deepEqual(readPackageParam("?package=pypi:fixture-web"), { registry: "pypi", name: "fixture-web" });
    assert.deepEqual(readPackageParam("?package=npm%3A%40fixture-scope%2Ftypes"), { registry: "npm", name: "@fixture-scope/types" });
  });

  it("ignores anything it cannot use", () => {
    for (const search of ["", "?package=", "?package=fixture-web", "?package=gem:fixture-web", "?package=pypi:", "?package=pypi:two%20words", `?package=pypi:${"a".repeat(215)}`, "?other=pypi:x"]) {
      assert.equal(readPackageParam(search), null, search);
    }
  });

  it("writes what it reads back", () => {
    for (const asked of [
      { registry: "pypi", name: "fixture-web" },
      { registry: "npm", name: "@fixture-scope/types" },
    ] as const) {
      assert.deepEqual(readPackageParam(writePackageParam(asked)), asked);
    }
    assert.equal(writePackageParam(null), "");
  });
});

describe("packageLabel", () => {
  it("names the registry in words", () => {
    assert.equal(packageLabel("pypi", "fixture-web"), "fixture-web on PyPI");
    assert.equal(packageLabel("npm", "fixture-server"), "fixture-server on npm");
  });
});

describe("describePackageCache", () => {
  const now = Date.parse("2026-11-12T12:00:00Z");
  it("says when the registry last answered", () => {
    const note = describePackageCache({ cache_status: "fresh", fetched_at: "2026-11-12T11:55:00Z", registry: "pypi" }, now);
    assert.deepEqual(note, { label: "Live", text: "Read from PyPI 5 minutes ago.", tone: "ok" });
  });

  it("says a stale copy may be out of date, with its real age", () => {
    const note = describePackageCache({ cache_status: "stale", fetched_at: "2026-11-12T09:00:00Z", registry: "npm" }, now);
    assert.equal(note.tone, "warn");
    assert.equal(note.label, "Stale");
    assert.equal(note.text, "npm could not be reached, so this is from 3 hours ago and may be out of date.");
  });
});

describe("isStandIn", () => {
  it("is true only when the API gave no registry page", () => {
    assert.equal(isStandIn({ registry_url: null }), true);
    assert.equal(isStandIn({ registry_url: "https://pypi.org/project/flask/" }), false);
  });
});

describe("describeLeftOut", () => {
  it("says what each kind is, with counts", () => {
    assert.deepEqual(
      describeLeftOut([
        { kind: "conditional", count: 2 },
        { kind: "development", count: 1 },
        { kind: "peer", count: 3 },
        { kind: "optional", count: 1 },
        { kind: "unusable", count: 1 },
      ]),
      [
        "2 requirements that apply only with an extra or on certain systems.",
        "1 development dependency, which only people working on the package need.",
        "3 peer dependencies, which the package expects whoever installs it to provide.",
        "1 optional dependency, installed only when they can be.",
        "1 entry that is not a name a registry could have issued.",
      ],
    );
  });

  it("agrees with a count above one", () => {
    assert.deepEqual(describeLeftOut([{ kind: "unusable", count: 4 }]), ["4 entries that are not names a registry could have issued."]);
  });

  it("is empty when nothing was left out", () => {
    assert.deepEqual(describeLeftOut([]), []);
  });
});

describe("describeRequirement", () => {
  it("shows the requirement as the package declared it", () => {
    assert.equal(describeRequirement(">=1.0,<2", true), ">=1.0,<2");
  });
  it("says what no requirement means", () => {
    assert.equal(describeRequirement(null, true), "any version");
    assert.equal(describeRequirement(null, false), "given as a link or path");
  });
});

describe("status words", () => {
  it("has words and help for every status", () => {
    for (const status of ["read", "unread", "unreadable"] as const) {
      assert.ok(STATUS_WORDS[status].length > 0);
      assert.ok(statusHelp(status, "pypi").length > 20);
    }
  });
  it("names the registry for a node that cannot be opened", () => {
    assert.match(statusHelp("unreadable", "npm"), /npm can look up/);
  });
  it("does not say an unopened package has no dependencies", () => {
    assert.match(statusHelp("unread", "pypi"), /does not mean it has no dependencies/);
  });
});

const read = (name: string, dependencies: PackageRead["dependencies"] = []): PackageRead => ({ registry: "pypi", name, version: "1.0.0", dependencies });
const dep = (name: string, readable = true) => ({ name, requirement: null, readable });

describe("describeGraph", () => {
  it("counts packages, links and what is still to open", () => {
    const graph = addRead(emptyGraph(), read("fixture-web", [dep("fixture-router"), dep("fixture-direct", false)])).graph;
    const summary = describeGraph(graph);
    assert.deepEqual([summary.packages, summary.dependencies, summary.unopened], [3, 2, 1]);
    assert.equal(summary.text, "3 packages and 2 dependency links, 1 not opened yet.");
  });

  it("says so when everything that can be opened has been", () => {
    const graph = addRead(emptyGraph(), read("fixture-web", [])).graph;
    assert.equal(describeGraph(graph).text, "1 package and 0 dependency links, every package that can be opened has been.");
  });
});

describe("describeRead", () => {
  it("says how many dependencies were listed and how many are new", () => {
    assert.equal(
      describeRead({ name: "fixture-router", registry: "pypi", dependencies: [dep("a"), dep("b")] }, 1, 0),
      "fixture-router lists 2 dependencies; 1 is new to the graph.",
    );
    assert.equal(
      describeRead({ name: "fixture-router", registry: "pypi", dependencies: [dep("a")] }, 0, 0),
      "fixture-router lists 1 dependency; none are new to the graph.",
    );
  });

  it("says a package lists none", () => {
    assert.equal(describeRead({ name: "fixture-utils", registry: "pypi", dependencies: [] }, 0, 0), "fixture-utils lists no dependencies.");
  });

  it("adds what was not drawn when the graph is full", () => {
    assert.match(describeRead({ name: "fixture-wide", registry: "pypi", dependencies: [dep("a"), dep("b")] }, 1, 1), /1 dependency of fixture-wide was not drawn, because the graph is full\.$/);
    assert.equal(describeNotDrawn("fixture-wide", 3), "3 dependencies of fixture-wide were not drawn, because the graph is full.");
  });
});

describe("describeNode", () => {
  it("gives the name, the status and the links", () => {
    const graph = addRead(emptyGraph(), read("fixture-web", [dep("fixture-router")])).graph;
    const router = graph.nodes.get("pypi:fixture-router");
    assert.ok(router);
    assert.equal(describeNode(router, 0, 1), "fixture-router. Not opened yet. needed by 1");
    const web = graph.nodes.get("pypi:fixture-web");
    assert.ok(web);
    assert.equal(describeNode(web, 1, 0), "fixture-web. Dependencies read. depends on 1");
  });
});

describe("packageFailure", () => {
  it("says the package is not on that registry", () => {
    const failure = packageFailure({ code: "not_found", message: "package not found" }, "pypi", "fixture-nope");
    assert.equal(failure.title, "No package called fixture-nope on PyPI");
    assert.match(failure.body, /switch registry/);
    assert.equal(failure.retryable, false);
  });

  it("says the name was refused, with an example for the registry", () => {
    assert.match(packageFailure({ code: "validation_error" }, "npm", "x").body, /@types\/node/);
    assert.match(packageFailure({ code: "validation_error" }, "pypi", "x").body, /flask/);
  });

  it("says the registry is limiting requests, with the wait and a retry", () => {
    const failure = packageFailure({ code: "upstream_rate_limited", retryAfterSeconds: 60 }, "npm", "fixture-limited");
    assert.equal(failure.title, "npm is limiting requests");
    assert.equal(failure.wait, "Wait 1 minute.");
    assert.equal(failure.retryable, true);
    assert.match(failure.body, /already opened stay on the page/);
  });

  it("uses the API's fixed text for an unusable answer and offers a retry", () => {
    const failure = packageFailure({ code: "upstream_error", message: "PyPI returned a response VYRON could not read", requestId: "req-1" }, "pypi", "fixture-broken");
    assert.equal(failure.title, "PyPI did not give a usable answer");
    assert.equal(failure.body, "PyPI returned a response VYRON could not read.");
    assert.equal(failure.requestId, "req-1");
    assert.equal(failure.retryable, true);
  });

  it("explains a server with registries switched off", () => {
    const failure = packageFailure({ code: "service_unavailable", message: "package registry access is switched off on this server" }, "pypi", "x");
    assert.equal(failure.title, "Package registries are switched off");
    assert.match(failure.body, /--no-registries/);
    assert.equal(failure.retryable, false);
  });

  it("falls back to the shared words, and to a network error for something that is not an error object", () => {
    assert.equal(packageFailure({ code: "timeout", retryable: true }, "pypi", "x").retryable, true);
    assert.ok(packageFailure(undefined, "pypi", "x").title.length > 0);
    assert.ok(packageFailure("boom", "npm", "x").body.length > 0);
  });

  it("never repeats text from a registry for a code it does not know", () => {
    const failure = packageFailure({ code: "internal_error", message: "<b>from the registry</b>" }, "pypi", "x");
    assert.ok(!failure.body.includes("registry"));
  });
});

describe("githubRepoFromUrl", () => {
  it("extracts owner/repo from a github.com project URL", () => {
    assert.equal(githubRepoFromUrl("https://github.com/pallets/flask"), "pallets/flask");
    assert.equal(githubRepoFromUrl("https://github.com/pallets/flask/tree/main#readme"), "pallets/flask");
    assert.equal(githubRepoFromUrl("https://www.github.com/expressjs/express.git"), "expressjs/express");
  });

  it("ignores non-GitHub URLs, user-only paths, and invalid values", () => {
    assert.equal(githubRepoFromUrl(null), null);
    assert.equal(githubRepoFromUrl("https://flask.palletsprojects.com/"), null);
    assert.equal(githubRepoFromUrl("https://github.com/pallets"), null);
    assert.equal(githubRepoFromUrl("javascript:alert(1)"), null);
  });
});
