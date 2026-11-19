/**
 * Compile-time assertions for the client's types. This file is never executed; `tsc -p
 * tsconfig.check.json` fails if a line marked @ts-expect-error stops being an error (the contract
 * got looser) or an unmarked line stops compiling (the contract got stricter).
 */
import type {
  Entity,
  EntityKind,
  PackageLeftOut,
  PackageReleases,
  PackageReport,
  ReleaseEntry,
  ReleaseLeftOut,
} from "../../types/src/api.ts";
import { VyronClient, walkPages } from "./client.ts";

export async function typeAssertions(client: VyronClient): Promise<void> {
  // Valid calls compile, and responses are typed from the contract.
  const meta = await client.get("get_meta");
  const name: string = meta.name;
  const entity: Entity = await client.get("get_entity", { path: { entity_id: "x" } });
  const kind: EntityKind = entity.kind;
  await client.get("list_entities", { query: { kind: ["language", "framework"], limit: 5 } });
  await client.get("graph_path", { query: { from: "a", to: "b" } });
  const report: PackageReport = await client.get("get_package_dependencies", {
    path: { registry: "pypi" },
    query: { name: "fixture-web" },
  });
  const left: PackageLeftOut | undefined = report.left_out[0];
  void left;
  const history: PackageReleases = await client.get("get_package_releases", {
    path: { registry: "npm" },
    query: { name: "fixture-web" },
  });
  const newest: ReleaseEntry | undefined = history.releases[history.releases.length - 1];
  const status: "normal" | "yanked" | "deprecated" | undefined = newest?.status;
  const dropped: ReleaseLeftOut | undefined = history.left_out[0];
  const meaning: "first_file_upload" | "publish_time" = history.date_meaning;
  void status;
  void dropped;
  void meaning;
  for await (const page of walkPages(client, "list_entities", {})) {
    const first: Entity | undefined = page.items[0];
    void first;
  }
  void name;
  void kind;

  // @ts-expect-error unknown operation id
  await client.get("no_such_operation");
  // @ts-expect-error a path parameter is required
  await client.get("get_entity");
  // @ts-expect-error path parameters belong only to operations that declare them
  await client.get("get_meta", { path: { entity_id: "x" } });
  // @ts-expect-error limit is a number
  await client.get("list_entities", { query: { limit: "5" } });
  // @ts-expect-error unknown query parameter
  await client.get("list_entities", { query: { colour: "red" } });
  // @ts-expect-error kind must be a real entity kind
  await client.get("list_entities", { query: { kind: ["gadget"] } });
  // @ts-expect-error the registry is a path parameter, so it cannot be left out
  await client.get("get_package_dependencies");
  // @ts-expect-error the release history is also keyed by registry
  await client.get("get_package_releases");
  // @ts-expect-error a release has a status, not a boolean yanked flag
  void newest?.yanked;
  // @ts-expect-error a body is required for POST operations that take one
  await client.get("create_research_run");
  // @ts-expect-error only collection operations can be walked
  walkPages(client, "get_meta", {});
  // @ts-expect-error a walked operation still needs its path parameters
  walkPages(client, "get_entity_timeline", {});
  // @ts-expect-error the response has no such field
  void meta.nonexistent;
}
